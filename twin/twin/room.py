"""A capture, read as a room: which way is up, how big it is, and what you can stand on.

All of the hard work is `akitech/splat`'s `ground.py`, which RANSACs planes out of the cloud
and picks up by how *layered* the scene is along each candidate normal. That indirection is
not decoration -- on the playroom capture the true up is
``(-0.002, -0.392, +0.920)``, oblique to every axis, so no named transform finds it and no
histogram along x, y or z does either.

**Two numbers here are assumptions, not measurements, and everything downstream rides on
them.**

- **Scale** comes from assuming the ceiling is ``room_height_m`` above the floor. A splat
  capture has no intrinsic scale; Marble worlds report no ``metric_scale_factor`` either
  (see ``capture/NOTES.md``). Get this wrong and a 30 cm arm is a doll or a crane.
- **Which end is up** comes from `ground.py`'s rule that the floor is the denser extreme.
  That rule is usually right and is not always right: on one of the desk captures a colour
  test disagreed with it. So ``flip`` is a first-class argument rather than a debugging
  afterthought, and the viewer is the honest check.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from . import deps

#: What the ceiling is assumed to be, floor to ceiling. `ground.py`'s own default, and the
#: same figure `web/src/scene/ground.ts` uses.
ROOM_HEIGHT_M = 2.6

#: A surface has to be at least this far off the floor to be somewhere an arm could stand
#: rather than the floor itself seen twice.
MIN_STANDING_HEIGHT_M = 0.25

#: ...and no higher than this, or it is a ceiling, a shelf or a light fitting.
MAX_STANDING_HEIGHT_M = 1.40


@dataclass(frozen=True)
class Surface:
    """One flat horizontal patch, in metres, in the aligned frame."""

    height: float
    centre: tuple[float, float]
    half_extent: tuple[float, float]
    splats: int
    area: float
    is_floor: bool

    def describe(self) -> str:
        what = "floor" if self.is_floor else "surface"
        return (
            f"{what:<8} z={self.height:5.2f}m  {self.area:5.2f} m2  "
            f"{self.splats:>7,} splats  half=({self.half_extent[0]:.2f}, {self.half_extent[1]:.2f})"
        )


@dataclass
class Room:
    """A capture, upright and in metres, with the surfaces read out of it."""

    path: Path
    positions: np.ndarray
    scale: float
    up_original: tuple[float, float, float]
    floor_z: float
    ceiling_z: float | None
    surfaces: list[Surface]
    flipped: bool

    @property
    def floor(self) -> Surface:
        return next(s for s in self.surfaces if s.is_floor)

    def standable(self) -> list[Surface]:
        """Surfaces an arm could plausibly stand on, best supported first.

        Ordered by splat count rather than area: a big thin band of noise can span more
        square metres than a real worktop, and the count is what says the surface was
        actually observed.
        """
        candidates = [
            s
            for s in self.surfaces
            if not s.is_floor
            and MIN_STANDING_HEIGHT_M <= s.height - self.floor_z <= MAX_STANDING_HEIGHT_M
        ]
        return sorted(candidates, key=lambda s: s.splats, reverse=True)

    def report(self) -> str:
        up = self.up_original
        lines = [
            f"{self.path.name}: {len(self.positions):,} splats",
            f"  up (original frame) [{up[0]:+.3f} {up[1]:+.3f} {up[2]:+.3f}]"
            + ("  FLIPPED" if self.flipped else ""),
            f"  scale {self.scale:.4f} m/unit   floor z={self.floor_z:.3f}"
            + (f"   ceiling z={self.ceiling_z:.2f}" if self.ceiling_z else "   no ceiling"),
        ]
        lines += [f"  {s.describe()}" for s in sorted(self.surfaces, key=lambda s: s.height)]
        standable = self.standable()
        lines.append(
            f"  -> {len(standable)} surface(s) an arm could stand on"
            + (f", best at z={standable[0].height:.2f}m" if standable else "")
        )
        return "\n".join(lines)


def _surface(raw: Any) -> Surface:
    return Surface(
        height=float(raw.height),
        centre=(float(raw.centre[0]), float(raw.centre[1])),
        half_extent=(float(raw.half_extent[0]), float(raw.half_extent[1])),
        splats=int(raw.splats),
        area=float(raw.area),
        is_floor=bool(raw.is_floor),
    )


def read(
    path: str | Path, *, room_height_m: float = ROOM_HEIGHT_M, flip: bool = False
) -> Room:
    """Load a capture and stand it up, in metres, with the floor at z = 0."""
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"no capture at {path}")

    splat_io = deps.splat_io()
    ground = deps.ground()

    splats = splat_io.load_ply(str(path))
    aligned, room = ground.align_scene(
        splats.position, room_height_m=room_height_m, flip_up=flip
    )

    return Room(
        path=path,
        positions=aligned,
        scale=float(room.scale),
        up_original=tuple(float(v) for v in room.up),
        floor_z=float(room.floor_z),
        ceiling_z=None if room.ceiling_z is None else float(room.ceiling_z),
        surfaces=[_surface(s) for s in room.surfaces],
        flipped=flip,
    )


__all__ = [
    "MAX_STANDING_HEIGHT_M",
    "MIN_STANDING_HEIGHT_M",
    "ROOM_HEIGHT_M",
    "Room",
    "Surface",
    "read",
]
