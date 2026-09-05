"""MJCF generation: the invariants, and what the joints actually do when driven.

The prototype's ``scripts/check_joints.py`` did this by hand against one reference file and
printed a table. Its docstring is right that opening the viewer and moving the sliders is the
correct instinct — and that it does not survive a refactor at 2pm. So the same checks live
here, headless and asserted, and they run over every stored schema rather than one.

Each of the four MuJoCo gotchas this generator is built around is silent when broken: the
model still compiles and still looks plausible. That is what these tests are for.
"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET

import mujoco
import numpy as np
import pytest

from app.mjcf import PANEL, SchemaError, compile_model, to_xml
from app.mjcf.build import _panel_offset
from app.schema import FALLBACK_DIR, half_extents, mass_kg

SCHEMA_PATHS = sorted(FALLBACK_DIR.glob("*.json"))


def load(name: str) -> dict:
    return json.loads((FALLBACK_DIR / f"{name}.json").read_text(encoding="utf-8"))


@pytest.fixture
def dishwasher() -> dict:
    return load("dishwasher")


def named(model, kind, name: str) -> int:
    return mujoco.mj_name2id(model, kind, name)


def at(model, data, **qpos) -> None:
    """Pin the named joints and recompute kinematics. No dynamics, no settling."""
    mujoco.mj_resetData(model, data)
    for name, value in qpos.items():
        jid = named(model, mujoco.mjtObj.mjOBJ_JOINT, name)
        data.qpos[model.jnt_qposadr[jid]] = value
    mujoco.mj_forward(model, data)


# ---- it loads at all ----------------------------------------------------------------------


@pytest.mark.parametrize("path", SCHEMA_PATHS, ids=lambda p: p.stem)
def test_every_stored_schema_compiles_in_mujoco(path):
    """Generating XML and loading it are different claims. This is the second one."""
    schema = json.loads(path.read_text(encoding="utf-8"))
    model = compile_model(schema)
    assert model.nbody >= 2, "worldbody plus at least the object itself"


def test_a_schema_that_does_not_validate_never_reaches_mujoco(dishwasher):
    dishwasher["parts"][0]["anchor"] = "somewhere_vague"
    with pytest.raises(SchemaError) as exc:
        to_xml(dishwasher)
    assert any("bad anchor" in e for e in exc.value.errors)


# ---- gotcha 1: a body with no joint is welded ---------------------------------------------


def test_a_free_object_falls_and_a_fixed_one_does_not():
    """The difference between "physicalise" doing something and doing nothing at all."""
    crate = load("crate")
    model = compile_model(crate, floor=False)
    data = mujoco.MjData(model)
    bid = named(model, mujoco.mjtObj.mjOBJ_BODY, "crate")
    mujoco.mj_forward(model, data)  # xpos is not populated until kinematics have run
    start = data.xpos[bid].copy()
    for _ in range(200):
        mujoco.mj_step(model, data)
    assert data.xpos[bid][2] < start[2] - 0.05, "a free body must fall under gravity"

    welded = dict(crate, mobility="fixed")
    model = compile_model(welded, floor=False)
    data = mujoco.MjData(model)
    bid = named(model, mujoco.mjtObj.mjOBJ_BODY, "crate")
    mujoco.mj_forward(model, data)  # xpos is not populated until kinematics have run
    start = data.xpos[bid].copy()
    for _ in range(200):
        mujoco.mj_step(model, data)
    assert data.xpos[bid][2] == pytest.approx(start[2]), "a fixed body is welded to the world"


def test_a_free_object_dropped_above_the_floor_comes_to_rest():
    """Falling is half of it. Settling rather than tunnelling or jittering is the other half."""
    crate = dict(load("crate"), mass=20.0)
    model = compile_model(crate)
    data = mujoco.MjData(model)
    bid = named(model, mujoco.mjtObj.mjOBJ_BODY, "crate")
    for _ in range(1500):  # 3 seconds at 0.002
        mujoco.mj_step(model, data)
    assert abs(data.qvel).max() < 1e-2, "must settle, not keep jittering"
    hz = half_extents(crate)[2]
    assert data.xpos[bid][2] == pytest.approx(hz, abs=5e-3), "must rest ON the floor, not in it"


# ---- gotcha 2: the body origin sits on the joint anchor -----------------------------------


def test_the_hinge_body_origin_is_the_anchor_and_the_joint_is_at_zero(dishwasher):
    """Put the origin on the anchor and anchor the joint at 0 0 0, and the two can never
    disagree. The failure mode otherwise is a door that pivots about its own centre."""
    root = ET.fromstring(to_xml(dishwasher))
    door = root.find(".//body[@name='door']")
    hx, _, hz = half_extents(dishwasher)

    assert [float(v) for v in door.get("pos").split()] == pytest.approx([hx, 0.0, -hz])
    assert door.find("joint").get("pos") == "0 0 0"


def test_autolimits_is_on_and_the_range_actually_binds(dishwasher):
    """Without autolimits, every range above is silently ignored and the door spins freely.

    Asserting the attribute is present is weak -- assert the limit is enforced by the solver.
    """
    assert ET.fromstring(to_xml(dishwasher)).find("compiler").get("autolimits") == "true"

    model = compile_model(dishwasher)
    jid = named(model, mujoco.mjtObj.mjOBJ_JOINT, "door_j")
    assert model.jnt_limited[jid] == 1, "the joint must be limited, not merely given a range"
    assert model.jnt_range[jid] == pytest.approx([0.0, np.deg2rad(90.0)])


def test_ranges_cross_into_mujoco_as_radians_from_degrees_on_the_schema(dishwasher):
    """The schema says 90. MuJoCo's qpos is radians. compiler angle="degree" does the
    conversion, and this is the assertion that it happened exactly once."""
    model = compile_model(dishwasher)
    jid = named(model, mujoco.mjtObj.mjOBJ_JOINT, "door_j")
    assert model.jnt_range[jid][1] == pytest.approx(np.pi / 2)
    assert model.jnt_range[jid][1] != pytest.approx(90.0)


# ---- gotcha 3: box size is half-extents ---------------------------------------------------


def test_box_size_is_half_extents_not_full_width():
    """A full width here builds an object exactly twice the size, which reads as a scale bug
    somewhere else entirely."""
    crate = load("crate")
    hx, hy, hz = half_extents(crate)
    model = compile_model(crate)
    gid = named(model, mujoco.mjtObj.mjOBJ_GEOM, "crate_body")
    assert model.geom_size[gid][:3] == pytest.approx([hx, hy, hz])
    assert crate["frame"]["size"] == pytest.approx([2 * hx, 2 * hy, 2 * hz])


# ---- gotcha 4: friction is the pair maximum -----------------------------------------------


def test_friction_is_written_on_the_floor_as_well_as_the_object(dishwasher):
    """MuJoCo takes the elementwise MAXIMUM across a contact pair, so setting it on the
    object alone does nothing whenever the floor is slicker."""
    schema = dict(dishwasher, friction=1.4)
    model = compile_model(schema)
    floor = named(model, mujoco.mjtObj.mjOBJ_GEOM, "floor")
    panel = named(model, mujoco.mjtObj.mjOBJ_GEOM, "door_panel")
    assert model.geom_friction[floor][0] == pytest.approx(1.4)
    assert model.geom_friction[panel][0] == pytest.approx(1.4)


@pytest.mark.parametrize("path", SCHEMA_PATHS, ids=lambda p: p.stem)
def test_no_geom_is_left_on_the_default_friction(path):
    schema = json.loads(path.read_text(encoding="utf-8"))
    root = ET.fromstring(to_xml(schema))
    for geom in root.iter("geom"):
        if geom.get("name") is None:
            continue
        assert geom.get("friction") is not None, f"{geom.get('name')} has no explicit friction"


# ---- mass ---------------------------------------------------------------------------------


def test_a_stated_mass_is_the_mass_the_solver_uses():
    """"a wooden crate, heavy" has to turn into a number, and that number has to survive."""
    crate = dict(load("crate"), mass=45.0)
    model = compile_model(crate)
    bid = named(model, mujoco.mjtObj.mjOBJ_BODY, "crate")
    assert model.body_mass[bid] == pytest.approx(45.0)


def test_an_unstated_mass_follows_the_measured_volume():
    """A selected wardrobe and a selected mug must not weigh the same when the model
    declines to say."""
    big = {"object": "wardrobe", "frame": {"size": [1.0, 0.6, 2.0]}}
    small = {"object": "mug", "frame": {"size": [0.1, 0.1, 0.1]}}
    assert mass_kg(big) > 100 * mass_kg(small)


# ---- the generalised panel geometry -------------------------------------------------------


def test_a_bottom_hinged_door_extends_up_and_a_top_hinged_lid_extends_down():
    """The prototype hardcoded "up", which is right for a dishwasher and upside down for a
    bin. The panel now derives from where its anchor sits on the front face."""
    half = (0.12, 0.115, 0.15)
    assert _panel_offset((0.12, 0.0, -0.15), half)[2] == pytest.approx(+0.15)
    assert _panel_offset((0.12, 0.0, +0.15), half)[2] == pytest.approx(-0.15)
    assert _panel_offset((0.12, +0.115, 0.0), half)[1] == pytest.approx(-0.115)
    assert _panel_offset((0.12, -0.115, 0.0), half)[1] == pytest.approx(+0.115)


def test_the_bins_top_hinged_lid_stays_within_its_own_height_when_shut():
    """A lid whose panel extended upward would stick a full object-height out of the top.

    Concretely: the bin is 0.3 m tall, so its lid panel must not reach above z = 0.15 in the
    object's own frame when closed.
    """
    model = compile_model(load("bin"), floor=False)
    data = mujoco.MjData(model)
    at(model, data, lid_j=0.0)
    gid = named(model, mujoco.mjtObj.mjOBJ_GEOM, "lid_panel")
    _, _, hz = half_extents(load("bin"))
    assert data.geom_xpos[gid][2] < hz, "a shut lid must sit inside the object's own height"


def test_a_handle_runs_along_the_hinge_axis(dishwasher):
    """So it is grabbed across the swing rather than along it. Left-right for a lid,
    up-down for a side-hung door."""
    root = ET.fromstring(to_xml(dishwasher))
    fromto = [float(v) for v in root.find(".//geom[@name='door_handle']").get("fromto").split()]
    start, end = np.array(fromto[:3]), np.array(fromto[3:])
    span = end - start
    # The dishwasher door's axis is left_right = (0, 1, 0).
    assert abs(span[1]) > 0.05, "the handle must have real length along the hinge axis"
    assert abs(span[0]) < 1e-9 and abs(span[2]) < 1e-9, "and none across it"


# ---- the sweep: drive every joint through its range ----------------------------------------


def shell_half(schema: dict) -> np.ndarray:
    return np.array(half_extents(schema))


@pytest.mark.parametrize("path", SCHEMA_PATHS, ids=lambda p: p.stem)
def test_no_hinged_part_sweeps_back_through_its_own_shell(path):
    """The acceptance criterion, generalised over every stored object.

    A hinge with the wrong axis sign, or a panel offset in the wrong direction, produces a
    door that swings *backwards into the cavity* rather than outward. It looks like a door
    opening right up until you notice it is inside the machine. So: sample the full range,
    and assert the panel's centre never enters the shell's own box.
    """
    schema = json.loads(path.read_text(encoding="utf-8"))
    hinges = [p for p in schema.get("parts", []) if p.get("joint") == "hinge"]
    if not hinges:
        pytest.skip("no hinged parts")

    model = compile_model(schema, floor=False)
    data = mujoco.MjData(model)
    half = shell_half(schema)
    obj = named(model, mujoco.mjtObj.mjOBJ_BODY, str(schema["object"]))

    for part in hinges:
        lo, hi = part["range"]
        gid = named(model, mujoco.mjtObj.mjOBJ_GEOM, f"{part['name']}_panel")
        for angle in np.linspace(lo, hi, 25):
            at(model, data, **{f"{part['name']}_j": np.deg2rad(angle)})
            local = data.geom_xpos[gid] - data.xpos[obj]
            inside = np.all(np.abs(local) < half - PANEL)
            assert not inside, (
                f"{path.stem}: {part['name']} panel centre is inside the shell at "
                f"{angle:.0f} deg (local {np.round(local, 4)}, half-extents {half})"
            )


@pytest.mark.parametrize("path", SCHEMA_PATHS, ids=lambda p: p.stem)
def test_every_slide_travels_its_full_range_along_its_own_axis(path):
    """A tray that travels the wrong way, or diagonally, is an axis resolved wrongly."""
    schema = json.loads(path.read_text(encoding="utf-8"))
    slides = [p for p in schema.get("parts", []) if p.get("joint") == "slide"]
    if not slides:
        pytest.skip("no sliding parts")

    model = compile_model(schema, floor=False)
    data = mujoco.MjData(model)

    for part in slides:
        lo, hi = part["range"]
        bid = named(model, mujoco.mjtObj.mjOBJ_BODY, part["name"])
        at(model, data, **{f"{part['name']}_j": lo})
        closed = data.xpos[bid].copy()
        at(model, data, **{f"{part['name']}_j": hi})
        travel = data.xpos[bid] - closed

        assert np.linalg.norm(travel) == pytest.approx(hi - lo, abs=1e-6)
        moving = np.argmax(np.abs(travel))
        off_axis = np.delete(travel, moving)
        assert np.allclose(off_axis, 0.0, atol=1e-9), f"{part['name']} travels diagonally"


@pytest.mark.parametrize("path", SCHEMA_PATHS, ids=lambda p: p.stem)
def test_every_button_springs_back_to_rest_and_stops_ringing(path):
    """A button is a sprung slide. Stiffness returns it; damping is what stops it ringing.

    A button that does not return is the mechanism behind the unsatisfiable-precondition
    class of dead twins the validator rejects.

    The tolerance is a fraction of travel rather than an absolute, because a button on a
    VERTICAL axis genuinely sags under its own weight: the bin's pedal settles 0.4 mm low,
    which is exactly ``m*g/k`` for a 1.6 g cap on a 40 N/m spring. That is correct physics,
    not a weak spring, and an absolute tolerance would either fail it or be so loose that it
    stopped catching a button which never returned at all.
    """
    schema = json.loads(path.read_text(encoding="utf-8"))
    buttons = [p for p in schema.get("parts", []) if p.get("joint") == "button"]
    if not buttons:
        pytest.skip("no buttons")

    model = compile_model(schema, floor=False)
    data = mujoco.MjData(model)

    for part in buttons:
        jid = named(model, mujoco.mjtObj.mjOBJ_JOINT, f"{part['name']}_j")
        adr = model.jnt_qposadr[jid]
        lo, hi = model.jnt_range[jid]

        mujoco.mj_resetData(model, data)
        data.qpos[adr] = lo  # press it fully
        mujoco.mj_forward(model, data)
        for _ in range(400):  # 0.8 s, no external force
            mujoco.mj_step(model, data)

        travel = abs(hi - lo)
        assert data.qpos[adr] == pytest.approx(hi, abs=0.05 * travel), (
            f"{part['name']} did not return: rests at {data.qpos[adr] * 1000:+.3f} mm, "
            f"{abs(data.qpos[adr] - hi) / travel:.1%} of its {travel * 1000:.1f} mm travel"
        )
        assert abs(data.qvel[model.jnt_dofadr[jid]]) < 1e-3, f"{part['name']} is still ringing"


# ---- affordances --------------------------------------------------------------------------


@pytest.mark.parametrize("path", SCHEMA_PATHS, ids=lambda p: p.stem)
def test_every_declared_affordance_becomes_a_site(path):
    schema = json.loads(path.read_text(encoding="utf-8"))
    wanted = {
        aff["site"]
        for part in schema.get("parts", [])
        for aff in (part.get("affordances") or [])
    }
    if not wanted:
        pytest.skip("no affordances")

    model = compile_model(schema)
    got = {mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_SITE, i) for i in range(model.nsite)}
    assert wanted <= got


def test_a_grasp_site_lands_on_the_handle_rather_than_the_panel(dishwasher):
    """Sites are named by the schema and placed by the generator, so a plan targeting
    'handle_grasp' keeps working when the geometry is regenerated. It has to actually be on
    the handle."""
    model = compile_model(dishwasher, floor=False)
    data = mujoco.MjData(model)
    at(model, data, door_j=0.0)
    site = data.site_xpos[named(model, mujoco.mjtObj.mjOBJ_SITE, "handle_grasp")]
    handle = data.geom_xpos[named(model, mujoco.mjtObj.mjOBJ_GEOM, "door_handle")]
    assert np.linalg.norm(site - handle) < 0.01


# ---- the reference model ------------------------------------------------------------------


def test_the_generated_dishwasher_matches_the_hand_written_reference_behaviour(dishwasher):
    """The prototype's reference XML was written by hand BEFORE the generator, and checked
    joint by joint. Reproducing its *behaviour* is what says the resolvers are right.

    Geom names differ by design: the shell's left and right walls were swapped in the
    prototype, since +y is left in a right-handed frame with +x front. See PORTING.md.
    """
    model = compile_model(dishwasher, floor=False)
    data = mujoco.MjData(model)
    hinge = data.xpos[named(model, mujoco.mjtObj.mjOBJ_BODY, "door")]

    at(model, data, door_j=0.0)
    shut = data.site_xpos[named(model, mujoco.mjtObj.mjOBJ_SITE, "handle_grasp")].copy()
    assert shut[2] - hinge[2] > 0.20, "closed: the handle is high above the hinge line"

    at(model, data, door_j=np.deg2rad(90))
    open_ = data.site_xpos[named(model, mujoco.mjtObj.mjOBJ_SITE, "handle_grasp")].copy()
    assert open_[0] - hinge[0] > 0.20, "open: the panel is horizontal, pointing away (+x)"
    assert abs(open_[2] - hinge[2]) < 0.05, "open: and level with the hinge"


@pytest.mark.parametrize("path", SCHEMA_PATHS, ids=lambda p: p.stem)
def test_an_objects_parts_do_not_collide_with_each_other(path):
    """A door panel and a button share the front face; a rack slides through a shut door.

    Left to the solver these produce standing penetrations, and the force holding an
    overlapping panel apart also holds a sprung button permanently depressed -- which reads
    as a broken spring. Parts are constrained by their joint ranges, not by bumping into
    their neighbours, so the pairs are excluded outright.
    """
    schema = json.loads(path.read_text(encoding="utf-8"))
    if not schema.get("parts"):
        pytest.skip("no parts to exclude")

    model = compile_model(schema, floor=False)
    data = mujoco.MjData(model)
    mujoco.mj_forward(model, data)
    assert data.ncon == 0, (
        f"{path.stem}: {data.ncon} contacts between parts of one object, worst penetration "
        f"{-min(data.contact[c].dist for c in range(data.ncon)) * 1000:.2f} mm"
    )


def test_excluding_internal_contacts_does_not_disable_contact_with_the_world():
    """The exclusions are per body pair. An object must still land on the floor."""
    model = compile_model(dict(load("crate"), mass=20.0))
    data = mujoco.MjData(model)
    for _ in range(1500):
        mujoco.mj_step(model, data)
    assert data.ncon > 0, "the crate must be resting on something"


def test_a_door_swinging_through_its_full_range_never_fights_its_own_shell(dishwasher):
    """The behaviour the exclusions buy: a door that opens freely rather than one that
    grinds against the frame it is hinged to."""
    model = compile_model(dishwasher, floor=False)
    data = mujoco.MjData(model)
    for angle in np.linspace(0, 90, 25):
        at(model, data, door_j=np.deg2rad(angle))
        assert data.ncon == 0, f"door contacts its own shell at {angle:.0f} deg"
