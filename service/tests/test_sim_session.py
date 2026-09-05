"""A live session: what it reports, what it preserves across a rebuild, and how fast it runs.

The rebuild tests are the ones that matter. MuJoCo models are immutable, so physicalising a
second object recompiles the first one too — and the obvious implementation silently resets
everything already in the scene. A door that was open snaps shut the moment the user
physicalises a crate across the room, which looks like a physics glitch and is a data loss.
"""

from __future__ import annotations

import json
import pathlib

import mujoco
import numpy as np
import pytest

from app.mjcf import SchemaError
from app.protocol import PhysicsObject, PoseBatch, Selection, WorldFrame
from app.schema import FALLBACK_DIR
from app.sim.session import MAX_CATCHUP, TIMESTEP, Session, real_time_factor

FIXTURES = pathlib.Path(__file__).resolve().parents[2] / "contract" / "fixtures"
GROUND = -1.42


def load(name: str) -> dict:
    return json.loads((FALLBACK_DIR / f"{name}.json").read_text(encoding="utf-8"))


@pytest.fixture
def world() -> WorldFrame:
    return WorldFrame(up=(0.0, 0.0, 1.0), groundHeight=GROUND, sceneScale=1.0)


@pytest.fixture
def selection() -> Selection:
    payload = json.loads(
        (FIXTURES / "client" / "selection.commit.json").read_text(encoding="utf-8")
    )
    return Selection.model_validate(payload["selection"])


def crate_selection(at=(0.0, 0.0, GROUND + 1.0), half=(0.25, 0.25, 0.2)) -> Selection:
    """A selection with an identity frame, for when the orientation is not the point."""
    return Selection(
        id="sel_crate",
        splatCount=1000,
        centroid=at,
        axes=(1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0),
        halfExtents=half,
    )


@pytest.fixture
def session(world) -> Session:
    return Session(world=world)


# ---- an empty session is a working session -------------------------------------------------


def test_a_session_with_nothing_in_it_still_steps(session):
    """The service builds one of these the moment a scene loads, before anything is selected."""
    session.advance(0.05)
    assert session.step_count > 0
    assert session.poses().poses == ()


def test_a_session_refuses_a_world_it_cannot_simulate():
    from app.mjcf.scene import SceneError

    with pytest.raises(SceneError):
        Session(world=WorldFrame(up=(0.0, 0.9, 0.4), groundHeight=0.0, sceneScale=1.0))


# ---- what physicalise reports --------------------------------------------------------------


def test_physicalize_returns_something_the_wire_accepts(session, selection):
    obj = session.physicalize("obj_01", load("dishwasher"), selection)
    assert isinstance(obj, PhysicsObject)
    # Round-tripping through the wire format is the real check that it is sendable.
    PhysicsObject.model_validate(obj.model_dump(by_alias=True, mode="json"))


def test_the_shell_is_reported_first_and_the_parts_follow(session, selection):
    """Order is load-bearing: the client assigns a splat to the LAST region containing it,
    so the shell's "all" must come first to act as the fallback."""
    obj = session.physicalize("obj_01", load("dishwasher"), selection)
    assert obj.parts[0].body_name == "obj_01__dishwasher"
    assert obj.parts[0].splat_subset == "all"
    assert [p.body_name for p in obj.parts[1:]] == [
        "obj_01__door",
        "obj_01__rack",
        "obj_01__start_button",
    ]


def test_ranges_are_reported_in_schema_units_not_radians(session, selection):
    """MuJoCo's qpos is radians; the wire is degrees. The conversion happens at the socket
    boundary and nowhere else, so what the session reports must be the schema's own number."""
    obj = session.physicalize("obj_01", load("dishwasher"), selection)
    door = next(p for p in obj.parts if p.joint_type == "hinge")
    rack = next(p for p in obj.parts if p.joint_type == "slide")
    assert door.range == (0.0, 90.0)
    assert rack.range == (0.0, 0.18)


def test_a_shell_with_no_joint_reports_fixed_and_a_free_one_reports_free(session):
    fixed = session.physicalize("obj_01", load("dishwasher"), crate_selection())
    assert fixed.parts[0].joint_type == "fixed"
    free = session.physicalize("obj_02", load("crate"), crate_selection(at=(2.0, 0.0, GROUND + 1)))
    assert free.parts[0].joint_type == "free"
    assert free.parts[0].range is None


def test_each_part_reports_the_pose_it_actually_starts_at(session, selection):
    """Stage 8 stores each splat group relative to this pose. If it disagrees with the model
    by even a little, every group is offset and the splats sit beside their collision box."""
    obj = session.physicalize("obj_01", load("dishwasher"), selection)
    for part in obj.parts:
        bid = mujoco.mj_name2id(session.model, mujoco.mjtObj.mjOBJ_BODY, part.body_name)
        assert part.initial_pose.position == pytest.approx(session.data.xpos[bid], abs=1e-9)
        assert part.initial_pose.orientation == pytest.approx(session.data.xquat[bid], abs=1e-9)


def test_mass_and_friction_reach_the_wire(session):
    heavy = dict(load("crate"), mass=45.0, friction=1.1)
    obj = session.physicalize("obj_01", heavy, crate_selection())
    assert obj.mass_kg == pytest.approx(45.0)
    assert obj.friction == pytest.approx(1.1)


def test_the_label_is_readable_rather_than_a_slug(session):
    obj = session.physicalize("obj_01", load("drawers"), crate_selection())
    assert obj.label == "chest of drawers"


# ---- the rebuild --------------------------------------------------------------------------


def test_physicalising_a_second_object_does_not_reset_the_first(session, selection):
    """The one that bites. Adding a crate recompiles the model, and the naive version drops
    every existing joint back to zero -- so the dishwasher door the user just opened shuts."""
    session.physicalize("obj_01", load("dishwasher"), selection)
    jid = mujoco.mj_name2id(session.model, mujoco.mjtObj.mjOBJ_JOINT, "obj_01__door_j")
    session.data.qpos[session.model.jnt_qposadr[jid]] = np.deg2rad(60.0)
    mujoco.mj_forward(session.model, session.data)

    session.physicalize("obj_02", load("crate"), crate_selection(at=(3.0, 0.0, GROUND + 1)))

    jid = mujoco.mj_name2id(session.model, mujoco.mjtObj.mjOBJ_JOINT, "obj_01__door_j")
    held = session.data.qpos[session.model.jnt_qposadr[jid]]
    assert np.rad2deg(held) == pytest.approx(60.0, abs=1e-6), "the door must stay where it was"


def test_a_free_bodys_full_pose_survives_a_rebuild(session):
    """A freejoint carries seven qpos values, not one. Truncating to the first is an easy
    mistake and loses the object's orientation while keeping its position."""
    session.physicalize("obj_01", dict(load("crate"), mass=10.0), crate_selection())
    for _ in range(300):
        mujoco.mj_step(session.model, session.data)
    before = session.data.qpos[:7].copy()

    session.physicalize("obj_02", load("crate"), crate_selection(at=(4.0, 0.0, GROUND + 1)))

    jid = mujoco.mj_name2id(session.model, mujoco.mjtObj.mjOBJ_JOINT, "obj_01__crate_free")
    adr = session.model.jnt_qposadr[jid]
    assert session.data.qpos[adr : adr + 7] == pytest.approx(before, abs=1e-9)


def test_removing_an_object_takes_its_bodies_with_it(session, selection):
    session.physicalize("obj_01", load("dishwasher"), selection)
    session.physicalize("obj_02", load("crate"), crate_selection(at=(3.0, 0.0, GROUND + 1)))
    assert session.remove("obj_01") is True

    names = session.body_names()
    assert not any(n.startswith("obj_01__") for n in names)
    assert "obj_02__crate" in names


def test_removing_something_that_was_never_there_is_not_an_error(session):
    assert session.remove("obj_99") is False


def test_a_failed_physicalise_leaves_the_session_exactly_as_it_was(session, selection):
    """A schema that cannot be built must not take the running scene down with it."""
    session.physicalize("obj_01", load("dishwasher"), selection)
    before = list(session.body_names())

    with pytest.raises(SchemaError):
        session.physicalize("obj_02", {"object": "x", "parts": [{"joint": "warp"}]}, selection)

    assert session.body_names() == before
    assert "obj_02" not in session.objects


# ---- poses --------------------------------------------------------------------------------


def test_poses_cover_every_body_and_nothing_else(session, selection):
    obj = session.physicalize("obj_01", load("dishwasher"), selection)
    batch = session.poses()
    assert isinstance(batch, PoseBatch)
    assert [p.body_name for p in batch.poses] == [p.body_name for p in obj.parts]


def test_a_pose_batch_carries_simulated_time_not_wall_time(session):
    session.physicalize("obj_01", load("crate"), crate_selection())
    session.advance(MAX_CATCHUP)
    assert session.poses().t == pytest.approx(session.step_count * TIMESTEP, abs=1e-9)


def test_a_free_object_falls_and_settles_over_a_stream_of_batches(session):
    """The S6 acceptance criterion, one layer down: not a single pose, but a trajectory that
    descends and then stops."""
    session.physicalize("obj_01", dict(load("crate"), mass=20.0), crate_selection())
    heights = []
    for _ in range(60):  # 60 batches at ~30 Hz is two seconds
        session.advance(1 / 30)
        heights.append(session.poses().poses[0].position[2])

    assert heights[10] < heights[0] - 0.05, "it must visibly fall"
    assert heights[-1] == pytest.approx(heights[-5], abs=1e-3), "and then come to rest"
    assert heights[-1] > GROUND, "on the floor, not through it"


# ---- the clock ----------------------------------------------------------------------------


def test_advance_steps_at_the_fixed_timestep(session):
    taken = session.advance(0.05)
    assert taken == int(0.05 / TIMESTEP)
    assert session.step_count == taken


def test_a_long_gap_is_capped_rather_than_simulated_in_one_burst(session):
    """A service that was descheduled for a minute must not then block its own socket
    simulating a minute of physics before sending anything."""
    assert session.advance(60.0) == int(MAX_CATCHUP / TIMESTEP)


def test_pause_stops_the_clock_and_play_starts_it(session):
    session.control("pause")
    assert session.advance(0.05) == 0
    assert session.status().running is False

    session.control("play")
    assert session.advance(0.05) > 0
    assert session.status().running is True


def test_reset_returns_the_scene_to_where_it_started(session):
    session.physicalize("obj_01", dict(load("crate"), mass=10.0), crate_selection())
    start = session.poses().poses[0].position[2]
    session.advance(MAX_CATCHUP)
    assert session.poses().poses[0].position[2] < start

    session.control("reset")
    assert session.step_count == 0
    assert session.poses().t == 0.0
    assert session.poses().poses[0].position[2] == pytest.approx(start, abs=1e-9)


def test_an_unknown_control_action_is_refused(session):
    with pytest.raises(ValueError, match="unknown sim action"):
        session.control("rewind")


def test_the_simulation_runs_faster_than_real_time(session, selection):
    """S5's acceptance criterion. Below 1.0 the physics cannot keep up with the clock and
    every scene visibly lags, no matter what the broadcast rate is."""
    session.physicalize("obj_01", load("dishwasher"), selection)
    session.physicalize("obj_02", load("microwave"), crate_selection(at=(2.0, 0.0, GROUND + 0.5)))
    heavy = dict(load("crate"), mass=20.0)
    session.physicalize("obj_03", heavy, crate_selection(at=(4.0, 0.0, GROUND + 1)))

    rtf = real_time_factor(session, seconds=0.5)
    assert rtf > 1.0, f"real-time factor {rtf:.1f}x with three objects"


# ---- working a mechanism -------------------------------------------------------------------


def test_a_hinge_is_driven_in_degrees(session, selection):
    """Most of a room is fitted, and no amount of gravity opens a bolted-in dishwasher. The
    interaction for those is to work the mechanism directly."""
    session.physicalize("obj_01", load("dishwasher"), selection)
    assert session.set_joint("obj_01__door", 45.0) is True
    assert session.joint_value("obj_01__door") == pytest.approx(45.0, abs=1e-6)

    jid = mujoco.mj_name2id(session.model, mujoco.mjtObj.mjOBJ_JOINT, "obj_01__door_j")
    raw = session.data.qpos[session.model.jnt_qposadr[jid]]
    assert raw == pytest.approx(np.deg2rad(45.0)), "qpos is radians; the wire is degrees"


def test_a_slide_is_driven_in_metres_not_radians(session, selection):
    """The conversion is per joint TYPE. A slide put through radians() moves 57 times too far."""
    session.physicalize("obj_01", load("dishwasher"), selection)
    session.set_joint("obj_01__rack", 0.12)
    assert session.joint_value("obj_01__rack") == pytest.approx(0.12, abs=1e-9)

    jid = mujoco.mj_name2id(session.model, mujoco.mjtObj.mjOBJ_JOINT, "obj_01__rack_j")
    assert session.data.qpos[session.model.jnt_qposadr[jid]] == pytest.approx(0.12)


def test_a_joint_cannot_be_driven_past_its_own_limit(session, selection):
    """A slider that could exceed the range would put the door through the shell."""
    session.physicalize("obj_01", load("dishwasher"), selection)
    session.set_joint("obj_01__door", 400.0)
    assert session.joint_value("obj_01__door") == pytest.approx(90.0, abs=1e-6)
    session.set_joint("obj_01__door", -50.0)
    assert session.joint_value("obj_01__door") == pytest.approx(0.0, abs=1e-6)


def test_driving_a_joint_moves_the_body_the_client_is_watching(session, selection):
    """The pose stream is how the splats find out. If the body does not move, nothing does."""
    session.physicalize("obj_01", load("dishwasher"), selection)
    before = next(p for p in session.poses().poses if p.body_name == "obj_01__door")
    session.set_joint("obj_01__door", 90.0)
    after = next(p for p in session.poses().poses if p.body_name == "obj_01__door")
    assert after.orientation != before.orientation


def test_a_driven_joint_stays_put_rather_than_springing_back(session, selection):
    """Velocity is zeroed with the position, or the door carries the momentum of a value that
    changed instantly and flies off its own limit."""
    session.physicalize("obj_01", load("dishwasher"), selection)
    session.set_joint("obj_01__door", 60.0)
    session.advance(MAX_CATCHUP)
    assert session.joint_value("obj_01__door") == pytest.approx(60.0, abs=1.0)


def test_driving_a_joint_that_does_not_exist_is_refused_not_crashed(session, selection):
    session.physicalize("obj_01", load("dishwasher"), selection)
    assert session.set_joint("obj_01__nonsense", 10.0) is False
    assert session.joint_value("obj_01__nonsense") is None
