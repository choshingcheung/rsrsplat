"""Objects placed into the room the browser measured.

``build.py`` makes one object in isolation, standing on a plane at z = 0, which is what a
test or the MuJoCo viewer wants. This is the other case: several objects, at the positions
and orientations the user's selections measured, on the floor the browser found.

**Scene coordinates are metric and z-up.** The browser establishes that at load -- it owns
the Gaussians and has to pick a render frame anyway -- so nothing here converts units or
rotates gravity. The incoming ``WorldFrame`` is validated rather than trusted, because a
capture that arrives tilted would otherwise produce a scene where gravity points at a wall
and every object slides quietly into a corner. See DECISIONS.md.

The one thing that is genuinely variable is the floor's height: a fitted ground plane is
rarely at z = 0.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Any

import numpy as np

from ..protocol import Obstacle, Selection, WorldFrame
from ..schema import friction, half_extents, validate, with_measured_frame
from ..splat import quat
from .build import SchemaError, build_object, exclude_internal_contacts

#: How far from z-up a scene may arrive before it is an error rather than noise. Generous:
#: a fitted plane is never exact, but it is never 5 degrees out either.
UP_TOLERANCE_DEG = 2.0

#: How far ``sceneScale`` may sit from 1.0. Anything else means the browser did not scale
#: the capture to metres and every mass, every extent and every gravity term is wrong.
SCALE_TOLERANCE = 1e-3

GRAVITY = 9.81


class SceneError(ValueError):
    """The world frame is not one this service can simulate in."""


@dataclass(frozen=True)
class SceneObject:
    """One physicalised object, ready to be placed.

    ``object_id`` prefixes every body name, so two crates in one scene do not collide in the
    namespace and the pose stream can address each one.
    """

    object_id: str
    schema: dict[str, Any]
    position: tuple[float, float, float]
    orientation: tuple[float, float, float, float]
    #: The measured collision shape, in the object's own frame. Empty falls back to the
    #: schema's box or shell.
    shape: tuple = ()

    @property
    def prefix(self) -> str:
        return f"{self.object_id}__"


def check_world(world: WorldFrame) -> None:
    """Reject a world frame that is not metric and z-up, saying which part is wrong."""
    up = np.asarray(world.up, dtype=float)
    norm = float(np.linalg.norm(up))
    if norm == 0.0:
        raise SceneError("world.up is a zero vector")

    cosine = float(np.clip(up[2] / norm, -1.0, 1.0))
    off_by = float(np.degrees(np.arccos(cosine)))
    if off_by > UP_TOLERANCE_DEG:
        raise SceneError(
            f"world.up is {off_by:.1f} degrees from +z ({np.round(up / norm, 4).tolist()}). "
            f"Scene coordinates must be z-up: the browser aligns the capture at load. "
            f"Simulating this frame would point gravity at a wall."
        )

    if abs(world.scene_scale - 1.0) > SCALE_TOLERANCE:
        raise SceneError(
            f"world.sceneScale is {world.scene_scale:g}, not 1.0. Scene coordinates must "
            f"already be metres: the browser scales the capture at load. Every mass and "
            f"extent downstream assumes SI."
        )


def placement_from_selection(selection: Selection) -> tuple[
    tuple[float, float, float], tuple[float, float, float, float]
]:
    """A selection's centroid and canonical axes, as an MJCF position and quaternion.

    The selection's axes are column-major with column 0 = +front, column 1 = +left,
    column 2 = +up -- which is exactly the object's own frame in ``build.py``. So the matrix
    whose columns are those axes IS the local-to-world rotation, and needs no reordering.

    The wire guarantees a right-handed set, but a reflection here would silently mirror the
    object, so it is checked rather than assumed.
    """
    axes = np.asarray(selection.axes, dtype=float).reshape(3, 3).T  # column-major on the wire
    det = float(np.linalg.det(axes))
    if det < 0:
        raise SceneError(
            f"selection {selection.id!r} has a left-handed axis set (determinant {det:+.4f}). "
            f"That is a reflection, not a rotation, and would mirror the object. The client "
            f"is meant to flip a column before sending."
        )

    q = quat.from_mat(axes.astype(np.float32))
    return tuple(float(v) for v in selection.centroid), tuple(float(v) for v in q)


def object_from_selection(
    object_id: str, schema: dict[str, Any], selection: Selection
) -> SceneObject:
    """Bind a generated schema to what the selection actually measured.

    Language supplied the mechanism; this is where perception supplies the pose. The
    schema's guessed frame size is replaced by the selection's half-extents, and the object
    is placed at the selection's centroid in the selection's own orientation.
    """
    position, orientation = placement_from_selection(selection)
    measured = with_measured_frame(schema, tuple(selection.half_extents))
    errors = validate(measured)
    if errors:
        raise SchemaError(errors)
    return SceneObject(object_id, measured, position, orientation, selection.shape)


def build_scene(
    world: WorldFrame,
    objects: list[SceneObject],
    obstacles: tuple[Obstacle, ...] = (),
) -> ET.ElementTree:
    """A complete MJCF for a session: the room, and everything in it.

    ``obstacles`` is what makes the scanned room SOLID. A splat stops nothing -- there is no
    reference to Gaussians anywhere in a collision driver -- so without them the only solid
    thing in the scene is the ground plane, and an object knocked off a worktop falls through
    the worktop, through the floor it was standing on, and out of the world.

    They are static geoms in the worldbody rather than bodies, so they are welded by
    construction and cost the solver nothing to hold still.
    """
    check_world(world)

    mj = ET.Element("mujoco", model="rsrsplat_scene")
    ET.SubElement(mj, "compiler", angle="degree", autolimits="true")
    ET.SubElement(mj, "option", timestep="0.002", gravity=f"0 0 -{GRAVITY:g}")

    visual = ET.SubElement(mj, "visual")
    ET.SubElement(visual, "headlight", ambient="0.4 0.4 0.4", diffuse="0.6 0.6 0.6")

    default = ET.SubElement(mj, "default")
    ET.SubElement(default, "geom", rgba="0.75 0.75 0.78 1")
    ET.SubElement(default, "joint", damping="0.5")

    body = ET.SubElement(mj, "worldbody")
    ET.SubElement(body, "light", pos="0 0 3", dir="0 0 -1", directional="true")

    # Gotcha 4 again: friction is the elementwise maximum across a pair, so the floor takes
    # the highest friction of anything standing on it. A slick floor under a grippy crate
    # would otherwise make the crate slick.
    mu = max((friction(o.schema) for o in objects), default=0.6)
    ET.SubElement(
        body,
        "geom",
        name="floor",
        type="plane",
        pos=f"0 0 {world.ground_height:g}",
        size="20 20 0.1",
        rgba="0.25 0.25 0.28 1",
        friction=f"{mu:g} 0.005 0.0001",
    )

    fric = f"{mu:g} 0.005 0.0001"
    for obstacle in carve_obstacles(obstacles, objects):
        ET.SubElement(
            body,
            "geom",
            name=f"env_{obstacle.id}",
            type="box",
            pos=" ".join(f"{v:.4f}" for v in obstacle.position),
            size=" ".join(f"{v:.4f}" for v in obstacle.half_extents),
            friction=fric,
            # Faintly drawn, and only ever in the MuJoCo viewer: the browser never sees this
            # geometry. It exists to be collided with, not looked at.
            rgba="0.45 0.42 0.38 0.25" if obstacle.kind == "surface" else "0.4 0.42 0.5 0.10",
        )

    for obj in objects:
        placed = build_object(
            body,
            obj.schema,
            pos=obj.position,
            quat=obj.orientation,
            prefix=obj.prefix,
            shape=obj.shape,
        )
        exclude_internal_contacts(mj, placed)

    return ET.ElementTree(mj)


def to_xml(
    world: WorldFrame, objects: list[SceneObject], obstacles: tuple[Obstacle, ...] = ()
) -> str:
    tree = build_scene(world, objects, obstacles)
    ET.indent(tree, space="  ")
    return ET.tostring(tree.getroot(), encoding="unicode")


def compile_scene(
    world: WorldFrame, objects: list[SceneObject], obstacles: tuple[Obstacle, ...] = ()
):
    import mujoco

    return mujoco.MjModel.from_xml_string(to_xml(world, objects, obstacles))


def carve_obstacles(
    obstacles: tuple[Obstacle, ...],
    objects: list[SceneObject],
    margin: float = 0.01,
) -> tuple[Obstacle, ...]:
    """Drop the static geometry a physicalised object now occupies.

    The room's collision boxes are voxelised from the scan, and the scan still contains the
    object -- the browser cuts it from the RENDER, but the obstacles were measured before
    anyone selected anything. Leave them and the new dynamic body spawns inside a frozen
    copy of itself, which MuJoCo resolves the only way it can: by ejecting it at whatever
    speed the penetration implies. It reads as the object exploding on contact with nothing.

    Overlap is decided by separating axis over the six face normals -- three world, three of
    the object's -- and NOT the nine edge cross-products. That is deliberately incomplete in
    the safe direction: it can call a near-miss an overlap and carve a box that did not
    strictly need carving, which costs a little scenery. The opposite error leaves a body
    embedded in the room, which costs the demo.
    """
    if not objects:
        return obstacles

    boxes = []
    for obj in objects:
        rotation = quat.to_mat(np.asarray(obj.orientation, dtype=np.float32)).astype(float)
        half = np.asarray(half_extents(obj.schema), dtype=float) + margin
        boxes.append((np.asarray(obj.position, dtype=float), rotation, half))

    kept = tuple(o for o in obstacles if not any(_overlaps(o, *b) for b in boxes))
    return kept


def _overlaps(
    obstacle: Obstacle,
    centre: np.ndarray,
    rotation: np.ndarray,
    half: np.ndarray,
) -> bool:
    """Separating-axis test between a world-aligned obstacle and an oriented object box.

    ``rotation`` has the object's axes as COLUMNS, matching ``placement_from_selection``.
    """
    c = np.asarray(obstacle.position, dtype=float)
    h = np.asarray(obstacle.half_extents, dtype=float)
    d = c - centre

    # The three world axes: project the object's box onto each.
    for i in range(3):
        reach = float(np.abs(rotation[i, :]) @ half)
        if abs(d[i]) > h[i] + reach:
            return False

    # The three object axes: project the obstacle's box onto each.
    for j in range(3):
        axis = rotation[:, j]
        reach = float(np.abs(axis) @ h)
        if abs(float(d @ axis)) > half[j] + reach:
            return False

    return True


def resting_height(schema: dict[str, Any], ground_height: float) -> float:
    """The z at which this object's centroid sits with its base on the floor.

    Used to drop something at a plausible starting height rather than inside the ground --
    a body spawned below the plane is ejected upward on the first step, which looks like an
    explosion and is really a placement error.
    """
    return ground_height + half_extents(schema)[2]


__all__ = [
    "GRAVITY",
    "SCALE_TOLERANCE",
    "UP_TOLERANCE_DEG",
    "SceneError",
    "SceneObject",
    "build_scene",
    "carve_obstacles",
    "check_world",
    "compile_scene",
    "object_from_selection",
    "placement_from_selection",
    "resting_height",
    "to_xml",
]
