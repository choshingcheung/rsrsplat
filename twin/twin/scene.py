"""The room, the arm and the block, composed into one MuJoCo model.

The composition deliberately mirrors `akitech/app`'s own `cell/world.py`: the same arm MJCF,
the same static/dynamic split, the same 4 cm red cube on a free joint. Anything that works
here should behave the same way there.

What is different is where it all stands. `world.py` builds a fixed 36 x 28 cm table at the
origin; this puts the arm on a surface **measured out of a real capture**, at the height and
in the place the scan says it is. That is the whole point: the scanned room becomes the
robot's world.

**The splats are never made solid.** They are appearance. This reads geometry *out* of them
and builds ordinary MuJoCo geoms to match -- the same discipline `service/app/mjcf/scene.py`
follows on the other side of the repo, arrived at independently.

Two MuJoCo facts this file is built around, both silent when broken:

1. **The SO-101 description ships keyframes sized for the arm alone.** Adding a free-jointed
   block takes the model from nq=6 to nq=13 and those keyframes become the wrong length.
   mujoco 3.9+ tolerates it; 3.3 refuses to compile. They are dropped rather than resized,
   because nothing here uses them. Lifted verbatim from `world.py`, which learned it the
   hard way.
2. **A body with no joint is welded to the world.** The block gets a freejoint, so it falls
   and can be picked up; the room geoms deliberately get none, so they are immovable at no
   cost to the solver.
"""

from __future__ import annotations

from dataclasses import dataclass

import mujoco
import numpy as np

from .room import Room, Surface

#: The offscreen framebuffer, in pixels. Compile-time, and the ceiling on any render size.
OFFSCREEN = (1920, 1080)

#: A 4 cm cube, matching akitech's `sceneref.BLOCK_SIZE`, in half-extents.
BLOCK_HALF = (0.02, 0.02, 0.02)

#: akitech's `sceneref.BLOCK_RGBA`. The block is red because the demo says "the red block".
BLOCK_RGBA = (0.85, 0.2, 0.2, 1.0)

#: How far in front of the arm's base the block sits. Inside the SO-101's ~30 cm reach with
#: room to descend onto it rather than at the very edge of the envelope.
BLOCK_REACH_M = 0.22

#: Walls are drawn faintly and only ever in the MuJoCo viewer. They exist to be collided
#: with, not looked at.
WALL_RGBA = (0.40, 0.42, 0.50, 0.12)
SURFACE_RGBA = (0.45, 0.42, 0.38, 0.35)


@dataclass(frozen=True)
class Placement:
    """Where the arm stands and where the block starts, in the aligned metric frame."""

    arm: tuple[float, float, float]
    block: tuple[float, float, float]
    surface_z: float
    on_floor: bool

    def describe(self) -> str:
        where = "the floor" if self.on_floor else f"a surface at {self.surface_z:.2f}m"
        return (
            f"arm on {where} at ({self.arm[0]:+.2f}, {self.arm[1]:+.2f}, {self.arm[2]:.2f}), "
            f"block {BLOCK_REACH_M:.2f}m in front at "
            f"({self.block[0]:+.2f}, {self.block[1]:+.2f}, {self.block[2]:.2f})"
        )


def place(room: Room, surface: Surface | None = None) -> Placement:
    """Choose where the arm stands: a named surface, else the best one, else the floor.

    The block goes in front of the arm along -y, which is the direction the SO-101's home
    pose faces, so the first thing the arm does is not a 180 degree base rotation.
    """
    if surface is None:
        standable = room.standable()
        surface = standable[0] if standable else room.floor

    cx, cy = surface.centre
    top = surface.height

    # Keep both inside the patch that was actually observed, so the arm is not standing on
    # the far edge of a band that only exists as noise.
    reach = min(BLOCK_REACH_M, max(0.08, surface.half_extent[1] * 0.8))
    return Placement(
        arm=(cx, cy + reach * 0.5, top),
        block=(cx, cy - reach * 0.5, top + BLOCK_HALF[2] + 0.005),
        surface_z=top,
        on_floor=surface.is_floor,
    )


def _find_body(body, name: str):
    if body.name == name:
        return body
    for child in body.bodies:
        found = _find_body(child, name)
        if found is not None:
            return found
    return None


def _drop_keyframes(spec) -> None:
    """See fact 1 in the module docstring. The deletion API moved between mujoco versions."""
    for key in list(spec.keys):
        if hasattr(spec, "delete"):
            spec.delete(key)  # mujoco >= 3.9
        else:
            key.delete()  # mujoco 3.3


def build(
    room: Room,
    placement: Placement,
    *,
    walls: bool = True,
    surfaces: bool = True,
) -> mujoco.MjModel:
    """Compose and compile: the SO-101, the room it stands in, and the red block."""
    from robot_descriptions import so_arm100_mj_description

    spec = mujoco.MjSpec.from_file(so_arm100_mj_description.MJCF_PATH)
    _drop_keyframes(spec)

    # The offscreen framebuffer is fixed at compile time and defaults to 640x480, so a
    # renderer asked for anything larger fails at construction rather than downscaling.
    spec.visual.global_.offwidth = OFFSCREEN[0]
    spec.visual.global_.offheight = OFFSCREEN[1]

    # The description ships no light of its own, so a composed scene renders by headlight
    # alone: flat, and with nothing to read the shape of the arm against.
    spec.worldbody.add_light(
        name="key",
        type=mujoco.mjtLightType.mjLIGHT_DIRECTIONAL,
        pos=[0.0, 0.0, 3.0],
        dir=[0.0, 0.2, -1.0],
        diffuse=[0.7, 0.7, 0.7],
        specular=[0.2, 0.2, 0.2],
    )

    base = _find_body(spec.worldbody, "Base")
    if base is None:
        raise RuntimeError("the SO-101 description has no body named 'Base'")
    base.pos = list(placement.arm)

    floor = room.floor
    spec.worldbody.add_geom(
        name="room_floor",
        type=mujoco.mjtGeom.mjGEOM_PLANE,
        pos=[0.0, 0.0, room.floor_z],
        size=[8.0, 8.0, 0.05],
        rgba=[0.22, 0.22, 0.25, 1.0],
    )

    if surfaces:
        # Every observed surface except the floor becomes a thin slab: the thing the arm
        # stands on, and anything else a block could be knocked onto.
        for i, s in enumerate(room.surfaces):
            if s.is_floor:
                continue
            spec.worldbody.add_geom(
                name=f"surface_{i}",
                type=mujoco.mjtGeom.mjGEOM_BOX,
                pos=[s.centre[0], s.centre[1], s.height - 0.01],
                size=[max(s.half_extent[0], 0.05), max(s.half_extent[1], 0.05), 0.01],
                rgba=list(SURFACE_RGBA),
            )

    if walls:
        cx, cy = floor.centre
        hx, hy = floor.half_extent
        height = (room.ceiling_z or (room.floor_z + 2.6)) - room.floor_z
        for name, px, py, sx, sy in (
            ("xlo", cx - hx, cy, 0.05, hy),
            ("xhi", cx + hx, cy, 0.05, hy),
            ("ylo", cx, cy - hy, hx, 0.05),
            ("yhi", cx, cy + hy, hx, 0.05),
        ):
            spec.worldbody.add_geom(
                name=f"wall_{name}",
                type=mujoco.mjtGeom.mjGEOM_BOX,
                pos=[px, py, room.floor_z + height / 2],
                size=[sx, sy, height / 2],
                rgba=list(WALL_RGBA),
            )

    block = spec.worldbody.add_body(name="block", pos=list(placement.block))
    block.add_freejoint()
    block.add_geom(
        name="block_geom",
        type=mujoco.mjtGeom.mjGEOM_BOX,
        size=list(BLOCK_HALF),
        rgba=list(BLOCK_RGBA),
    )

    return spec.compile()


def block_position(model: mujoco.MjModel, data: mujoco.MjData) -> np.ndarray:
    bid = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, "block")
    if bid < 0:
        raise KeyError("no body named 'block' in this model")
    return np.array(data.xpos[bid], dtype=float)


def settle(model: mujoco.MjModel, data: mujoco.MjData, seconds: float = 0.5) -> None:
    """Let the scene come to rest before anything is asked of it."""
    for _ in range(int(seconds / model.opt.timestep)):
        mujoco.mj_step(model, data)


__all__ = [
    "BLOCK_HALF",
    "OFFSCREEN",
    "BLOCK_REACH_M",
    "BLOCK_RGBA",
    "Placement",
    "block_position",
    "build",
    "place",
    "settle",
]
