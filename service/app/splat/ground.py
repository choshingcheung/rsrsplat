"""Finding the floor, the up direction, and the surfaces you can put something on.

Ported from ``akitech/splat`` ``src/ground.py``, close to verbatim. Every threshold in here
carries a measurement behind it and none of them are the obvious value; paraphrasing this
module would have meant rediscovering all of it.

The three findings worth reading before changing anything:

* **Up has to be derived.** None of ``identity``, ``opencv_to_zup`` or ``yup_to_zup`` gives a
  clean floor for the playroom capture, because a COLMAP frame is not one of a small set of
  conventions. RANSAC the planes, then choose between their normals by how LAYERED the scene
  is along each -- 16 sharp layers along up against 4 along the wall normal.
* **Two plausible rules both fail here.** The single largest plane is a *wall* (18.8% of
  splats), and so is the largest parallel family, because one enormous wall outweighs four
  smaller horizontal ones.
* **Scale comes from floor-to-ceiling, not the bounding box.** A splat scene has no idea how
  big it is, so exactly one real number must come from outside, and ceiling height is the one
  a person can state without measuring. Scaling by "longest axis = 6 m" instead put this
  room's ceiling at 1.95 m and its floor 0.08 m off the ground.

And one that is a performance trap rather than a correctness one -- see :func:`align_splats`.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass
class Plane:
    normal: np.ndarray
    offset: float
    inliers: int

    def distance(self, points: np.ndarray) -> np.ndarray:
        return points @ self.normal + self.offset


@dataclass
class Surface:
    """A horizontal patch you could put an object on."""

    height: float                 # z, in the aligned frame
    centre: np.ndarray            # (x, y) of the patch
    half_extent: np.ndarray       # (x, y) half-size of its occupied footprint
    splats: int
    area: float                   # occupied area, not bounding-box area
    is_floor: bool = False

    @property
    def label(self) -> str:
        return "floor" if self.is_floor else f"surface at {self.height:.2f}m"


def find_planes(points: np.ndarray, count: int = 6, tol: float = 0.05,
                iters: int = 500, seed: int = 0) -> list[Plane]:
    """Sequential RANSAC: fit the biggest plane, drop its inliers, repeat."""
    rng = np.random.default_rng(seed)
    remaining = np.ascontiguousarray(points, dtype=np.float64)
    out: list[Plane] = []

    for _ in range(count):
        if len(remaining) < 200:
            break
        best_n, best_d, best_c = None, 0.0, 0
        for _ in range(iters):
            a, b, c = remaining[rng.choice(len(remaining), 3, replace=False)]
            n = np.cross(b - a, c - a)
            norm = np.linalg.norm(n)
            if norm < 1e-9:
                continue
            n = n / norm
            d = float(-n @ a)
            hits = int((np.abs(remaining @ n + d) < tol).sum())
            if hits > best_c:
                best_n, best_d, best_c = n, d, hits
        if best_n is None or best_c < 100:
            break
        out.append(Plane(best_n, best_d, best_c))
        remaining = remaining[np.abs(remaining @ best_n + best_d) >= tol]
    return out


def layer_score(points: np.ndarray, normal: np.ndarray, bins: int = 200) -> int:
    """How many sharp layers the scene has along `normal`. More layers means more horizontal."""
    proj = points @ normal
    lo, hi = np.percentile(proj, [0.5, 99.5])
    if hi - lo < 1e-6:
        return 0
    counts, _ = np.histogram(proj, bins=bins, range=(lo, hi))
    frac = counts / max(counts.sum(), 1)
    nonzero = frac[frac > 0]
    if not len(nonzero):
        return 0
    background = float(np.median(nonzero))
    return int(((frac > 4 * background) & (frac > 0.004)).sum())


def estimate_up(planes: list[Plane], points: np.ndarray,
                parallel_tol: float = 0.20) -> np.ndarray:
    """Which way is up, from how layered the scene is along each candidate plane normal."""
    if not planes:
        raise ValueError("no planes found — the cloud is too small or too noisy")

    candidates: list[np.ndarray] = []
    for p in planes:
        if not any(abs(float(p.normal @ c)) > 1 - parallel_tol for c in candidates):
            candidates.append(p.normal)

    up = np.array(max(candidates, key=lambda n: layer_score(points, n)), dtype=np.float64)
    up = up / np.linalg.norm(up)

    # Sign, deterministically. RANSAC returns a plane normal with an arbitrary sign, so without
    # this the same scene comes out upside down on a different seed -- measured: 4 seeds gave
    # +up and the fifth gave -up. Canonicalise on the largest component, then let
    # `floor_is_denser_end` decide which way is actually up.
    if up[int(np.argmax(np.abs(up)))] < 0:
        up = -up
    return up


def floor_is_denser_end(z: np.ndarray, slab: float = 0.06) -> bool:
    """True when the floor is at the LOW end of `z` — i.e. the alignment is right way up.

    **A floor is the denser extreme.** Objects collect on it and a capture observes it closely,
    while a ceiling is flat and featureless. Measured on the playroom scan: 34.7% of splats in
    the floor's extreme band against 14.8% in the ceiling's.

    This is a heuristic and it can be wrong — gravity is genuinely not recoverable from a splat
    cloud alone, which is why real capture pipelines take it from the device's IMU. So it is a
    *proposal*, and `align_scene(flip_up=True)` overrides it. That is the plan's own rule:
    human confirm on the axis, one click, and do not be precious about full automation.
    """
    lo, hi = np.percentile(z, [0.5, 99.5])
    span = max(hi - lo, 1e-9)
    low_end = float(((z >= lo) & (z < lo + slab * span / 2.6)).mean())
    high_end = float(((z > hi - slab * span / 2.6) & (z <= hi)).mean())
    return low_end >= high_end


def rotation_to_z(up: np.ndarray) -> np.ndarray:
    """A rotation matrix taking `up` to +z, with no roll of its own."""
    up = np.asarray(up, dtype=np.float64)
    up = up / np.linalg.norm(up)
    z = np.array([0.0, 0.0, 1.0])
    v = np.cross(up, z)
    s = np.linalg.norm(v)
    if s < 1e-9:
        return np.eye(3) if up @ z > 0 else np.diag([1.0, -1.0, -1.0])
    c = float(up @ z)
    vx = np.array([[0, -v[2], v[1]], [v[2], 0, -v[0]], [-v[1], v[0], 0]])
    return np.eye(3) + vx + vx @ vx * ((1 - c) / (s ** 2))


def horizontal_surfaces(points: np.ndarray, *, bin_h: float = 0.03,
                        min_splats: int = 400, cell: float = 0.06,
                        min_area: float = 0.05, slab: float = 0.04) -> list[Surface]:
    """Find flat horizontal patches: the floor, and anything you could set a mug on.

    Works on a histogram of height, because after alignment a horizontal surface is a spike in
    it. Each candidate band is then rasterised in xy so the reported extent is the **occupied**
    footprint rather than a bounding box — a table and a shelf on opposite walls share a height
    and must not merge into one enormous slab.
    """
    z = points[:, 2]
    lo, hi = float(z.min()), float(np.percentile(z, 99.5))
    # Pad the range by a bin at each end. Without it a scene that is a single flat band -- a
    # bare tabletop, a floor on its own -- collapses to a degenerate histogram, and any surface
    # sitting in the very first or very last bin is missed entirely.
    lo, hi = lo - bin_h, hi + bin_h
    nbins = max(8, int((hi - lo) / bin_h))
    counts, edges = np.histogram(z, bins=nbins, range=(lo, hi))

    # A band is a local maximum that is also well above the surrounding density.
    surfaces: list[Surface] = []
    for i in range(len(counts)):
        if counts[i] < min_splats:
            continue
        # Local maximum, and well above the scene's general density rather than its immediate
        # neighbourhood -- a broad surface makes its own neighbourhood dense, and comparing
        # against that found nothing at all.
        if counts[i] < counts[max(0, i - 4):min(len(counts), i + 5)].max():
            continue
        # Background is the MEAN over every bin, including empty ones. Using the median of the
        # non-empty bins fails exactly when the scene is mostly flat surfaces: with only a few
        # occupied bins, that median sits between the peaks and rejects all of them.
        background = counts.mean()
        if counts[i] < 3.0 * background:
            continue
        height = float((edges[i] + edges[i + 1]) / 2)
        band = points[np.abs(z - height) < slab]
        if len(band) < min_splats:
            continue

        gx = np.floor(band[:, 0] / cell).astype(np.int64)
        gy = np.floor(band[:, 1] / cell).astype(np.int64)
        occupied = {(int(a), int(b)) for a, b in zip(gx, gy, strict=True)}
        area = len(occupied) * cell * cell
        if area < min_area:
            continue
        surfaces.append(Surface(
            height=height,
            centre=np.array([band[:, 0].mean(), band[:, 1].mean()]),
            half_extent=np.array([(band[:, 0].max() - band[:, 0].min()) / 2,
                                  (band[:, 1].max() - band[:, 1].min()) / 2]),
            splats=len(band), area=area))

    surfaces.sort(key=lambda s: s.height)
    if surfaces:
        surfaces[0].is_floor = True
    return surfaces


def support_patch(points: np.ndarray, surface: Surface, *, cell: float = 0.05,
                  slab: float = 0.04, want: float = 0.16) -> tuple[np.ndarray, float]:
    """The most solid spot on a surface, and how wide the clear run around it is.

    Placing an object at a surface's *centroid* is wrong — a table with a gap in the middle, or a
    band that is really two shelves, both put it in mid-air. This finds the occupied cell with
    the largest fully-occupied square around it, which is somewhere the object can actually rest.
    """
    band = points[np.abs(points[:, 2] - surface.height) < slab]
    if len(band) == 0:
        return surface.centre, 0.0

    gx = np.floor(band[:, 0] / cell).astype(np.int64)
    gy = np.floor(band[:, 1] / cell).astype(np.int64)
    x0, y0 = gx.min(), gy.min()
    grid = np.zeros((gx.max() - x0 + 1, gy.max() - y0 + 1), dtype=bool)
    grid[gx - x0, gy - y0] = True

    radius = max(1, int(round(want / 2 / cell)))
    best, best_ij = -1, None
    pad = np.pad(grid, radius, constant_values=False)
    for i in range(grid.shape[0]):
        for j in range(grid.shape[1]):
            if not grid[i, j]:
                continue
            block = pad[i:i + 2 * radius + 1, j:j + 2 * radius + 1]
            score = int(block.sum())
            if score > best:
                best, best_ij = score, (i, j)
    if best_ij is None:
        return surface.centre, 0.0
    i, j = best_ij
    centre = np.array([(x0 + i + 0.5) * cell, (y0 + j + 0.5) * cell])
    clear = best / ((2 * radius + 1) ** 2)
    return centre, float(clear)


# --------------------------------------------------------------------------------------------
# From a raw cloud to a scene a physics engine can work in
# --------------------------------------------------------------------------------------------

@dataclass
class Room:
    """An aligned, scaled scene plus the geometry read out of it."""

    rotation: np.ndarray          # applied to the raw positions
    scale: float
    up: np.ndarray                # in the ORIGINAL frame, for the record
    floor_z: float
    ceiling_z: float | None
    surfaces: list[Surface]
    walls: list[Plane]            # in the aligned, scaled frame

    def report(self) -> str:
        lines = [f"up (original frame) [{self.up[0]:+.3f} {self.up[1]:+.3f} {self.up[2]:+.3f}]",
                 f"scale {self.scale:.4f}  floor z={self.floor_z:.3f}"
                 + (f"  ceiling z={self.ceiling_z:.2f}" if self.ceiling_z
                    else "  no ceiling found"),
                 f"{len(self.surfaces)} horizontal surfaces, {len(self.walls)} walls"]
        for s in self.surfaces:
            lines.append(f"   {s.label:<22} {s.area:5.2f} m2  {s.splats:>7,} splats")
        return "\n".join(lines)


def align_scene(positions: np.ndarray, *, room_height_m: float = 2.6,
                sample: int = 60_000, seed: int = 0,
                flip_up: bool = False) -> tuple[np.ndarray, Room]:
    """Derive up, stand the room upright, scale it, and put the floor on z=0.

    **Scale comes from floor-to-ceiling**, not from the bounding box. A splat scene has no idea
    how big it is, so exactly one real-world number has to come from outside, and ceiling height
    is the one a person can state without measuring anything. Scaling by "longest axis = 6m"
    instead put this room's ceiling at 1.95m and its floor 0.08m off the ground.

    Returns the transformed positions and the `Room` describing what was found.
    """
    pts = np.ascontiguousarray(positions, dtype=np.float64)
    rng = np.random.default_rng(seed)
    sub = pts[rng.choice(len(pts), min(sample, len(pts)), replace=False)]

    planes = find_planes(sub, count=6, seed=seed)
    up = estimate_up(planes, sub)

    # Which end of that axis is the floor? Proposed from density, overridable by the caller.
    if not floor_is_denser_end(sub @ up):
        up = -up
    if flip_up:
        up = -up

    rot = rotation_to_z(up)
    aligned = pts @ rot.T
    bands = horizontal_surfaces(aligned, min_splats=max(400, len(aligned) // 400))

    floor = bands[0].height if bands else float(np.percentile(aligned[:, 2], 0.5))
    ceiling = bands[-1].height if len(bands) > 1 else None

    scale = room_height_m / (ceiling - floor) if ceiling else 1.0
    aligned *= scale
    floor *= scale
    if ceiling:
        ceiling *= scale
    aligned[:, 2] -= floor
    aligned[:, 0] -= aligned[:, 0].mean()
    aligned[:, 1] -= aligned[:, 1].mean()

    surfaces = horizontal_surfaces(aligned, min_splats=max(400, len(aligned) // 400))
    walls = [p for p in planes if abs(float(p.normal @ up)) < 0.35]

    return aligned.astype(np.float32), Room(
        rotation=rot, scale=scale, up=up, floor_z=0.0,
        ceiling_z=(ceiling - floor) if ceiling else None,
        surfaces=surfaces, walls=walls)


def collision_mjcf(room: Room, positions: np.ndarray, *, wall_thickness: float = 0.05) -> str:
    """MJCF for the parts of the room a robot can actually hit.

    A splat stops nothing, so this is where the room becomes solid: a floor plane, a box for each
    detected surface's occupied footprint, and a wall box on each side of the scene's extent.
    Deliberately coarse — the twin is a type checker, and a room made of a dozen boxes rejects
    the same impossible plans a millimetre-accurate one would.
    """
    # Size the room from the FLOOR's own footprint, not the whole cloud. A splat scene always
    # has floaters -- reconstruction noise well outside the room -- and this capture's raw extent
    # is 8.25 m tall against a ceiling at 2.60 m. Walls placed on that would be metres past the
    # real ones, which is worse than having none.
    floor = next((s for s in room.surfaces if s.is_floor), None)
    if floor is not None:
        cx, cy = float(floor.centre[0]), float(floor.centre[1])
        hx, hy = float(floor.half_extent[0]), float(floor.half_extent[1])
    else:
        lo = np.percentile(positions, 2, axis=0)
        hi = np.percentile(positions, 98, axis=0)
        cx, cy = float((lo[0] + hi[0]) / 2), float((lo[1] + hi[1]) / 2)
        hx, hy = float((hi[0] - lo[0]) / 2), float((hi[1] - lo[1]) / 2)
    height = room.ceiling_z or 2.6

    out = [f'    <geom name="env_floor" type="plane" size="{hx + 0.5:.2f} {hy + 0.5:.2f} 0.1"'
           f' pos="{cx:.3f} {cy:.3f} 0" rgba="0.35 0.38 0.45 0.20"/>']

    for i, s in enumerate(room.surfaces):
        if s.is_floor or (room.ceiling_z and s.height > room.ceiling_z - 0.15):
            continue          # the floor is a plane already, and nothing rests on a ceiling
        out.append(
            f'    <geom name="env_surface_{i}" type="box"'
            f' size="{max(s.half_extent[0], 0.05):.3f} {max(s.half_extent[1], 0.05):.3f} 0.02"'
            f' pos="{s.centre[0]:.3f} {s.centre[1]:.3f} {s.height - 0.02:.3f}"'
            f' rgba="0.45 0.42 0.38 0.35"/>')

    for name, px, py, sx, sy in (
        ("xlo", cx - hx, cy, wall_thickness, hy), ("xhi", cx + hx, cy, wall_thickness, hy),
        ("ylo", cx, cy - hy, hx, wall_thickness), ("yhi", cx, cy + hy, hx, wall_thickness),
    ):
        out.append(f'    <geom name="env_wall_{name}" type="box"'
                   f' size="{max(sx, wall_thickness):.3f} {max(sy, wall_thickness):.3f}'
                   f' {height / 2:.3f}" pos="{px:.3f} {py:.3f} {height / 2:.3f}"'
                   f' rgba="0.4 0.42 0.5 0.10"/>')
    return "\n".join(out)


def align_splats(splats, *, room_height_m: float = 2.6, sample: int = 60_000,
                 seed: int = 0, flip_up: bool = False):
    """`align_scene`, applied to a whole splat cloud rather than bare positions.

    **Use this, not `align_scene`, when you are going to render.** A splat is an ellipsoid, so
    the room transform has to reach all three of its fields: positions move, per-splat
    quaternions rotate, and per-splat scales shrink with the room. Moving only the positions
    leaves every splat at its original size inside a room 0.27x smaller — which is not merely
    ugly, it is 40x slower to raytrace, because each ray now wades through a pile of oversized
    overlapping blobs. Measured: 88 ms a frame becomes 3510 ms.
    """
    from .ply import Splats
    from .quat import from_mat, mul

    aligned, room = align_scene(splats.position, room_height_m=room_height_m,
                                sample=sample, seed=seed, flip_up=flip_up)
    q = from_mat(room.rotation.astype(np.float32))
    return Splats(
        position=aligned,
        rotation=mul(q[None, :], splats.rotation),
        scale=(splats.scale * room.scale).astype(np.float32),
        rgba=splats.rgba.copy(),
    ), room
