"""Objects placed into the room the browser measured.

The interesting tests here are the two cross-checks against ``contract/fixtures/``. Those
fixtures were written by hand, computing the dishwasher's orientation and its door's hinge
position with a pen. This module arrives at both independently, from the selection's axes and
the schema's symbolic anchor. Agreement means the frame convention on the wire and the frame
convention in the generated MJCF are genuinely the same one -- which is the mismatch
PORTING.md hazard 2 was about, and the mismatch that is invisible in a viewer.
"""

from __future__ import annotations

import json
import pathlib

import mujoco
import numpy as np
import pytest

from app.mjcf import SchemaError
from app.mjcf.scene import (
    SceneError,
    SceneObject,
    check_world,
    compile_scene,
    object_from_selection,
    placement_from_selection,
    resting_height,
)
from app.protocol import Selection, WorldFrame
from app.schema import FALLBACK_DIR, half_extents

FIXTURES = pathlib.Path(__file__).resolve().parents[2] / "contract" / "fixtures"
GROUND = -1.42


def fixture(direction: str, name: str) -> dict:
    return json.loads((FIXTURES / direction / f"{name}.json").read_text(encoding="utf-8"))


def load(name: str) -> dict:
    return json.loads((FALLBACK_DIR / f"{name}.json").read_text(encoding="utf-8"))


@pytest.fixture
def selection() -> Selection:
    return Selection.model_validate(fixture("client", "selection.commit")["selection"])


@pytest.fixture
def world() -> WorldFrame:
    return WorldFrame.model_validate(fixture("client", "scene.load")["world"])


# ---- the world frame is validated, not trusted --------------------------------------------


def test_the_fixture_world_is_accepted(world):
    check_world(world)


def test_a_tilted_scene_is_rejected_with_the_angle_named():
    tilted = WorldFrame(up=(0.0, 0.34, 0.94), groundHeight=0.0, sceneScale=1.0)
    with pytest.raises(SceneError) as exc:
        check_world(tilted)
    assert "degrees from +z" in str(exc.value)
    assert "gravity at a wall" in str(exc.value), "the message must say why it matters"


def test_a_scene_still_in_capture_units_is_rejected():
    """The capture on hand is 27 x 34 x 34 arbitrary units. Simulating that as metres gives a
    34-metre room, and every mass and every settling time is wrong."""
    unscaled = WorldFrame(up=(0.0, 0.0, 1.0), groundHeight=0.0, sceneScale=0.21)
    with pytest.raises(SceneError, match="not 1.0"):
        check_world(unscaled)


def test_a_slightly_imperfect_up_is_tolerated():
    """A fitted ground plane is never exact. It is never five degrees out either."""
    check_world(WorldFrame(up=(0.005, 0.0, 0.999), groundHeight=0.0, sceneScale=1.0))


def test_a_zero_up_vector_is_rejected_rather_than_dividing_by_zero():
    with pytest.raises(SceneError, match="zero vector"):
        check_world(WorldFrame(up=(0.0, 0.0, 0.0), groundHeight=0.0, sceneScale=1.0))


# ---- placement, cross-checked against the hand-written fixtures ----------------------------


def test_placement_reproduces_the_fixtures_hand_computed_orientation(selection):
    """The fixture's quaternion was worked out with a pen, from "yawed 30 degrees about up".

    This derives it instead from the selection's three axis columns. They must agree, and if
    the wire ever swaps a column they will not.
    """
    position, orientation = placement_from_selection(selection)
    expected = fixture("server", "object.created")["object"]["parts"][0]["initialPose"]

    assert position == pytest.approx(expected["position"])
    assert orientation == pytest.approx(expected["orientation"], abs=1e-6)


def test_the_generated_hinge_lands_where_the_fixture_says_the_door_starts(selection):
    """The second cross-check, and the sharper one.

    The fixture put the door's body origin at the bottom front edge by hand. Here it comes
    out of the symbolic anchor "bottom_front_edge" resolved against the selection's measured
    half-extents and rotated by the selection's own frame. Two different routes to one point.
    """
    obj = object_from_selection("obj_01", load("dishwasher"), selection)
    model = compile_scene(WorldFrame(up=(0, 0, 1), groundHeight=GROUND, sceneScale=1.0), [obj])
    data = mujoco.MjData(model)
    mujoco.mj_forward(model, data)

    bid = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, "obj_01__door")
    expected = fixture("server", "object.created")["object"]["parts"][1]["initialPose"]
    assert data.xpos[bid] == pytest.approx(expected["position"], abs=1e-6)


def test_a_left_handed_axis_set_is_refused_rather_than_silently_mirroring(selection):
    """PCA eigenvectors come back with arbitrary sign. The client is meant to flip a column;
    if it ever stops, a reflection here would mirror the object and nothing would look wrong
    until a door opened on the wrong side."""
    flipped = [-v for v in selection.axes[3:6]]
    mirrored = selection.model_copy(
        update={"axes": (*selection.axes[:3], *flipped, *selection.axes[6:])}
    )
    with pytest.raises(SceneError, match="left-handed"):
        placement_from_selection(mirrored)


def test_the_measured_half_extents_replace_the_schemas_guess(selection):
    """Language supplies the mechanism; perception supplies the pose."""
    obj = object_from_selection("obj_01", load("dishwasher"), selection)
    assert obj.schema["frame"]["size"] == pytest.approx([0.60, 0.60, 0.85])
    assert load("dishwasher")["frame"]["size"] == [0.24, 0.23, 0.30], "the stored one is intact"


def test_a_selection_that_makes_the_schema_implausible_is_refused(selection):
    """A selection spanning half a room, with an appliance schema, is not an appliance."""
    huge = selection.model_copy(update={"half_extents": (2.0, 2.0, 2.0)})
    with pytest.raises(SchemaError):
        object_from_selection("obj_01", load("dishwasher"), huge)


# ---- the floor is where the browser found it ----------------------------------------------


def test_the_floor_sits_at_the_fitted_ground_height_not_at_zero(world):
    model = compile_scene(world, [])
    gid = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_GEOM, "floor")
    assert model.geom_pos[gid][2] == pytest.approx(world.ground_height)
    assert world.ground_height != 0.0, "the fixture must exercise a non-zero floor"


def test_a_dropped_object_settles_on_that_floor_within_a_bounded_time():
    """S4's acceptance criterion. Falling is easy; coming to rest, on the floor rather than
    through it, within a known number of steps, is the part that says the scene is sane."""
    crate = dict(load("crate"), mass=20.0)
    obj = SceneObject("obj_01", crate, (0.0, 0.0, GROUND + 1.0), (1.0, 0.0, 0.0, 0.0))
    world = WorldFrame(up=(0, 0, 1), groundHeight=GROUND, sceneScale=1.0)

    model = compile_scene(world, [obj])
    data = mujoco.MjData(model)
    bid = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, "obj_01__crate")

    settled_at = None
    for step in range(2000):  # 4 seconds at 0.002
        mujoco.mj_step(model, data)
        if settled_at is None and abs(data.qvel).max() < 1e-2 and step > 100:
            settled_at = step

    assert settled_at is not None and settled_at < 1500, f"still moving at step {settled_at}"
    assert data.xpos[bid][2] == pytest.approx(resting_height(crate, GROUND), abs=5e-3)
    assert data.xpos[bid][2] > GROUND, "it must rest ON the floor, not fall through it"


def test_nothing_tunnels_through_the_floor_on_the_way_down():
    """A heavy body at 0.002 can pass straight through a plane between two steps. If it ever
    does, the lowest z it reaches will be below the floor even though it settles above it."""
    crate = dict(load("crate"), mass=200.0)
    obj = SceneObject("obj_01", crate, (0.0, 0.0, GROUND + 2.0), (1.0, 0.0, 0.0, 0.0))
    model = compile_scene(WorldFrame(up=(0, 0, 1), groundHeight=GROUND, sceneScale=1.0), [obj])
    data = mujoco.MjData(model)
    bid = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, "obj_01__crate")

    lowest = np.inf
    for _ in range(2000):
        mujoco.mj_step(model, data)
        lowest = min(lowest, float(data.xpos[bid][2]))
    assert lowest > GROUND, f"reached z={lowest:.4f}, below the floor at {GROUND}"


# ---- several objects in one scene ----------------------------------------------------------


def test_two_objects_of_the_same_kind_do_not_collide_in_the_namespace():
    """MuJoCo's namespace is flat. Two crates called "crate" is a compile error at best and
    a pose stream addressing the wrong body at worst."""
    crate = load("crate")
    world = WorldFrame(up=(0, 0, 1), groundHeight=GROUND, sceneScale=1.0)
    model = compile_scene(
        world,
        [
            SceneObject("obj_01", crate, (0.0, 0.0, GROUND + 0.2), (1.0, 0.0, 0.0, 0.0)),
            SceneObject("obj_02", crate, (1.5, 0.0, GROUND + 0.2), (1.0, 0.0, 0.0, 0.0)),
        ],
    )
    names = {mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_BODY, i) for i in range(model.nbody)}
    assert {"obj_01__crate", "obj_02__crate"} <= names


def test_separate_objects_still_collide_with_each_other():
    """Only an object's OWN parts are excluded. Two crates must still stack."""
    crate = dict(load("crate"), mass=10.0)
    world = WorldFrame(up=(0, 0, 1), groundHeight=GROUND, sceneScale=1.0)
    hz = resting_height(crate, GROUND) - GROUND
    model = compile_scene(
        world,
        [
            SceneObject("obj_01", crate, (0.0, 0.0, GROUND + hz), (1.0, 0.0, 0.0, 0.0)),
            SceneObject(
                "obj_02", crate, (0.0, 0.0, GROUND + 3 * hz + 0.05), (1.0, 0.0, 0.0, 0.0)
            ),
        ],
    )
    data = mujoco.MjData(model)
    for _ in range(2000):
        mujoco.mj_step(model, data)

    top = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, "obj_02__crate")
    assert data.xpos[top][2] == pytest.approx(GROUND + 3 * hz, abs=1e-2), (
        "the upper crate must land on the lower one, not pass through it"
    )


def test_the_floor_takes_the_highest_friction_of_anything_standing_on_it():
    """MuJoCo takes the pair maximum, so a slick floor would make a grippy crate slick."""
    world = WorldFrame(up=(0, 0, 1), groundHeight=GROUND, sceneScale=1.0)
    grippy = dict(load("crate"), friction=1.5)
    model = compile_scene(
        world, [SceneObject("obj_01", grippy, (0.0, 0.0, 0.0), (1.0, 0.0, 0.0, 0.0))]
    )
    gid = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_GEOM, "floor")
    assert model.geom_friction[gid][0] == pytest.approx(1.5)


def test_an_empty_scene_is_still_a_valid_model(world):
    """The session builds one of these before anything has been physicalised."""
    model = compile_scene(world, [])
    assert model.ngeom >= 1
    mujoco.mj_step(model, mujoco.MjData(model))


def test_the_scene_declares_degrees_and_autolimits(world):
    """The same two compiler settings as the standalone path. A scene that quietly dropped
    autolimits would let every door in the room spin through its own shell."""
    import xml.etree.ElementTree as ET

    from app.mjcf.scene import to_xml

    compiler = ET.fromstring(to_xml(world, [])).find("compiler")
    assert compiler.get("angle") == "degree"
    assert compiler.get("autolimits") == "true"


# ---- the room is solid ----------------------------------------------------------------------


def obstacle(kind: str, position, half, name="o1"):
    from app.protocol import Obstacle

    return Obstacle(id=name, kind=kind, position=position, halfExtents=half)


def test_without_obstacles_a_scanned_room_stops_nothing(world):
    """The state this fixes, asserted so it cannot come back unnoticed.

    A splat stops nothing. With only a ground plane, a bottle knocked off a worktop passes
    straight through the worktop -- because to the solver there is no worktop.
    """
    crate = dict(load("crate"), mass=5.0)
    # Starting where a worktop would be, with no worktop declared.
    obj = SceneObject("obj_01", crate, (0.0, 0.0, GROUND + 1.2), (1.0, 0.0, 0.0, 0.0))
    model = compile_scene(world, [obj])
    data = mujoco.MjData(model)
    for _ in range(1500):
        mujoco.mj_step(model, data)

    bid = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, "obj_01__crate")
    assert data.xpos[bid][2] == pytest.approx(resting_height(crate, GROUND), abs=5e-3)


def test_a_declared_surface_actually_stops_things(world):
    """The acceptance criterion for making the room solid."""
    crate = dict(load("crate"), mass=5.0)
    worktop_z = GROUND + 0.9
    surface = obstacle("surface", (0.0, 0.0, worktop_z), (0.8, 0.4, 0.02), "surface_1")

    obj = SceneObject("obj_01", crate, (0.0, 0.0, GROUND + 1.6), (1.0, 0.0, 0.0, 0.0))
    model = compile_scene(world, [obj], (surface,))
    data = mujoco.MjData(model)
    for _ in range(1500):
        mujoco.mj_step(model, data)

    bid = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, "obj_01__crate")
    rest = worktop_z + 0.02 + half_extents(crate)[2]
    assert data.xpos[bid][2] == pytest.approx(rest, abs=1e-2), "it must land ON the worktop"
    assert data.xpos[bid][2] > GROUND + 0.5, "and not fall through to the floor"


def test_a_wall_stops_something_sliding_into_it(world):
    """Objects must stay in the room. Without walls a nudged bottle slides out of the scan."""
    crate = dict(load("crate"), mass=5.0, friction=0.05)
    wall = obstacle("wall", (1.2, 0.0, GROUND + 1.0), (0.05, 2.0, 1.0), "wall_xhi")

    obj = SceneObject("obj_01", crate, (0.0, 0.0, GROUND + 0.25), (1.0, 0.0, 0.0, 0.0))
    model = compile_scene(world, [obj], (wall,))
    data = mujoco.MjData(model)
    bid = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, "obj_01__crate")

    mujoco.mj_forward(model, data)
    data.qvel[0] = 4.0  # shove it hard at the wall
    for _ in range(1500):
        mujoco.mj_step(model, data)

    assert data.xpos[bid][0] < 1.2, f"it went through the wall, to x={data.xpos[bid][0]:.3f}"


def test_obstacles_are_static_geometry_rather_than_bodies(world):
    """Welded by construction: a worktop that could itself fall is not a worktop."""
    surface = obstacle("surface", (0.0, 0.0, GROUND + 0.9), (0.8, 0.4, 0.02), "surface_1")
    model = compile_scene(world, [], (surface,))

    gid = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_GEOM, "env_surface_1")
    assert gid >= 0, "the obstacle must reach the model"
    assert model.geom_bodyid[gid] == 0, "it must belong to the worldbody"
    assert model.nbody == 1, "obstacles add no bodies at all"


def test_obstacles_take_the_same_friction_as_the_floor(world):
    """MuJoCo takes the pair maximum, so a slick worktop makes a grippy crate slick."""
    grippy = dict(load("crate"), friction=1.5)
    surface = obstacle("surface", (0.0, 0.0, GROUND + 0.9), (0.8, 0.4, 0.02), "surface_1")
    model = compile_scene(
        world, [SceneObject("obj_01", grippy, (0, 0, 0), (1, 0, 0, 0))], (surface,)
    )
    gid = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_GEOM, "env_surface_1")
    assert model.geom_friction[gid][0] == pytest.approx(1.5)


# ---- the measured shape ----------------------------------------------------------------------


def shaped(selection, boxes):
    from app.protocol import ShapeBox

    return selection.model_copy(
        update={"shape": tuple(ShapeBox(center=c, halfExtents=h) for c, h in boxes)}
    )


def test_a_measured_shape_replaces_the_schemas_box(selection, world):
    """The whole point of voxelising: the collision volume is the OBJECT, not a description
    of it. The schema still decides mobility, mass and what articulates."""
    legs = [((0.2, 0.2, -0.3), (0.03, 0.03, 0.12)), ((-0.2, -0.2, -0.3), (0.03, 0.03, 0.12))]
    obj = object_from_selection("obj_01", load("crate"), shaped(selection, legs))
    model = compile_scene(world, [obj])

    for i in range(len(legs)):
        gid = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_GEOM, f"obj_01__crate_part{i}")
        assert gid >= 0, "every measured box must reach the model"
    assert mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_GEOM, "obj_01__crate_body") < 0, (
        "the schema's single box must not also be emitted"
    )


def test_an_empty_shape_falls_back_to_the_bounding_box(selection, world):
    """Segmentation can fail to find a clean component, and a box is the honest fallback.

    The fixture selection carries a shape, so it has to be cleared to exercise this at all --
    which is the right way round: a measured shape is the normal case now.
    """
    obj = object_from_selection("obj_01", load("crate"), selection.model_copy(update={"shape": ()}))
    model = compile_scene(world, [obj])
    assert mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_GEOM, "obj_01__crate_body") >= 0


def test_a_shaped_body_has_a_gap_a_bounding_box_would_have_filled(selection, world):
    """A table's bounding box contains the air between its legs, so nothing can be pushed
    under it and it never tips. Two legs and a top must leave the middle empty."""
    boxes = [
        ((0.0, 0.0, 0.38), (0.30, 0.30, 0.02)),   # top
        ((0.25, 0.25, 0.15), (0.03, 0.03, 0.21)),  # leg
        ((-0.25, -0.25, 0.15), (0.03, 0.03, 0.21)),
    ]
    obj = object_from_selection("obj_01", load("crate"), shaped(selection, boxes))
    model = compile_scene(world, [obj])
    data = mujoco.MjData(model)
    mujoco.mj_forward(model, data)

    body = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, "obj_01__crate")
    # A point under the top and between the legs, in the body's own frame.
    gap = data.xpos[body] + data.xmat[body].reshape(3, 3) @ np.array([0.0, 0.0, 0.0])

    for gid in range(model.ngeom):
        if model.geom_bodyid[gid] != body:
            continue
        local = model.geom_pos[gid]
        size = model.geom_size[gid]
        inside = all(abs(np.array([0.0, 0.0, 0.0])[a] - local[a]) <= size[a] for a in range(3))
        assert not inside, f"geom {gid} fills the gap a bounding box would have"
    assert gap is not None


def test_the_measured_shape_is_in_the_objects_own_frame(selection, world):
    """Boxes arrive in the selection's axes, so a rotated object's shape rotates with it
    rather than staying axis-aligned in the world."""
    boxes = [((0.25, 0.0, 0.0), (0.05, 0.05, 0.05))]
    obj = object_from_selection("obj_01", load("crate"), shaped(selection, boxes))
    model = compile_scene(world, [obj])
    data = mujoco.MjData(model)
    mujoco.mj_forward(model, data)

    gid = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_GEOM, "obj_01__crate_part0")
    body = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, "obj_01__crate")

    # Offset 0.25 along the selection's FRONT, which for this fixture is yawed 30 degrees.
    front = np.asarray(selection.axes[0:3])
    expected = data.xpos[body] + 0.25 * front
    assert data.geom_xpos[gid] == pytest.approx(expected, abs=1e-6)


# ---------------------------------------------------------------------------------------
# Carving the scan out from under a physicalised object
# ---------------------------------------------------------------------------------------


def test_the_scan_s_own_copy_of_an_object_is_carved_away():
    """The room's boxes were measured before anything was selected, object included."""
    from app.mjcf.scene import carve_obstacles

    crate = load("crate")
    obj = SceneObject("obj_01", crate, (1.0, 0.5, GROUND + 0.3), (1.0, 0.0, 0.0, 0.0))
    here = obstacle("solid", (1.0, 0.5, GROUND + 0.3), (0.08, 0.08, 0.08), "solid_1")
    far = obstacle("solid", (2.4, 0.5, GROUND + 0.3), (0.08, 0.08, 0.08), "solid_2")

    kept = carve_obstacles((here, far), [obj])
    assert [o.id for o in kept] == ["solid_2"]


def test_carving_follows_the_object_s_own_axes():
    """A rotated object is not an axis-aligned box, and carving it as one is wrong twice.

    A box turned 45 degrees reaches further along the diagonal than its half-extents suggest
    and less far along the world axes. Carving by a world-aligned bound would both leave
    scenery inside the object's corners and delete scenery beside its faces.
    """
    from app.mjcf.scene import carve_obstacles

    crate = load("crate")
    half = half_extents(crate)
    s2 = 2.0 ** 0.5 / 2
    turned = (s2, 0.0, 0.0, s2)  # 90 degrees about z, (w, x, y, z)

    obj = SceneObject("obj_01", crate, (0.0, 0.0, GROUND + 0.5), turned)
    # Just outside the object along its ROTATED front, which used to be world +y.
    outside = obstacle(
        "solid", (0.0, half[0] + 0.30, GROUND + 0.5), (0.02, 0.02, 0.02), "out"
    )
    inside = obstacle(
        "solid", (0.0, half[0] * 0.5, GROUND + 0.5), (0.02, 0.02, 0.02), "in"
    )

    kept = {o.id for o in carve_obstacles((outside, inside), [obj])}
    assert kept == {"out"}


def test_an_object_born_inside_the_scan_does_not_explode(world):
    """The failure this prevents, measured rather than argued.

    A body overlapping static geometry is ejected at whatever speed the penetration implies.
    ``build_scene`` carves first, so the same scene is quiet.
    """
    crate = dict(load("crate"), mass=5.0)
    rest = resting_height(crate, GROUND)
    obj = SceneObject("obj_01", crate, (0.0, 0.0, rest), (1.0, 0.0, 0.0, 0.0))

    # The scan's boxes, right through the middle of it.
    room = tuple(
        obstacle("solid", (0.0, 0.0, rest + dz), (0.1, 0.1, 0.04), f"solid_{i}")
        for i, dz in enumerate((-0.05, 0.0, 0.05))
    )

    model = compile_scene(world, [obj], room)
    data = mujoco.MjData(model)
    for _ in range(600):
        mujoco.mj_step(model, data)

    bid = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, "obj_01__crate")
    assert np.linalg.norm(data.xpos[bid][:2]) < 0.05  # did not shoot sideways
    assert data.xpos[bid][2] == pytest.approx(rest, abs=5e-3)  # still on the floor


def test_carving_leaves_the_room_alone(world):
    """Only what the object occupies goes. The floor it stands on must survive."""
    from app.mjcf.scene import carve_obstacles

    crate = load("crate")
    obj = SceneObject("obj_01", crate, (0.0, 0.0, GROUND + 0.4), (1.0, 0.0, 0.0, 0.0))
    floor = obstacle("solid", (0.0, 0.0, GROUND - 0.04), (2.0, 2.0, 0.04), "floor")
    wall = obstacle("wall", (2.0, 0.0, GROUND + 1.0), (0.05, 2.0, 1.0), "wall_xhi")

    kept = {o.id for o in carve_obstacles((floor, wall), [obj])}
    assert kept == {"floor", "wall_xhi"}

