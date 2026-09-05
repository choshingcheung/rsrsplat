"""Open a capture, or measure what it costs to draw.

    python -m app.view <capture.ply>              open the window
    python -m app.view <capture.ply> --bench      measure, and print the go/no-go table

``--bench`` is R1's acceptance criterion. MJWarp raytraces splats rather than rasterising
them, so frame cost grows with splat count in a way a browser rasteriser's does not, and the
whole desktop architecture rests on whether a usable budget is pleasant to drive. Measure it
before building anything on top.

Controls in the window:

    left drag     orbit
    right drag    pan
    scroll        zoom
    1 2 3 4       splat budget: 60k / 120k / 250k / 500k  (rebuilds; takes a moment)
    S             splats on / off
    Esc           quit
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from ..splat.ground import align_splats
from ..splat.ply import load
from ..splat.sample import drop_faint, subsample
from .scene import SplatScene, scene_xml
from .window import BUDGETS, Viewer


def prepare(path: Path, *, room_height: float, flip: bool) -> tuple:
    """Read a capture and stand it upright, at metre scale.

    Uses ``align_splats``, not ``align_scene``: a splat is an ellipsoid, so the room transform
    has to reach all three of its fields. Moving only the positions leaves every splat at its
    original size inside a smaller room, which is not merely ugly -- it is **40x slower to
    raytrace**, because each ray then wades through a pile of oversized overlapping blobs.
    Measured in the prototype: 88 ms a frame becomes 3510 ms.
    """
    print(f"reading {path.name} ...", flush=True)
    started = time.perf_counter()
    splats = load(path)
    print(f"  {len(splats):,} splats in {time.perf_counter() - started:.1f}s", flush=True)

    started = time.perf_counter()
    aligned, room = align_splats(splats, room_height_m=room_height, flip_up=flip)
    print(f"  aligned in {time.perf_counter() - started:.1f}s", flush=True)
    print("  " + room.report().replace("\n", "\n  "), flush=True)
    return aligned, room


def bench(splats, *, budgets=BUDGETS, resolutions=((640, 480), (960, 720)), frames: int = 20):
    """Frame cost across budgets and resolutions, on this machine.

    The number that matters is not the average but whether the worst case is drivable: frame
    cost is view dependent as much as splat count -- the prototype measured the same 80k
    budget at 43 ms looking out of a room and 177 ms looking into its dense side. This looks
    from one fixed viewpoint into the scene, which is the expensive direction.
    """
    print()
    print(f"{'splats':>10} " + " ".join(f"{w}x{h}".rjust(12) for w, h in resolutions))
    print("-" * (11 + 13 * len(resolutions)))

    for budget in budgets:
        subset = subsample(drop_faint(splats), budget)
        row = [f"{len(subset):>10,}"]
        for width, height in resolutions:
            scene = SplatScene.build(scene_xml(), subset, width=width, height=height)
            # One warm-up: the first call compiles kernels, about 4s on a cold cache.
            scene.push_pose()
            scene.render()

            started = time.perf_counter()
            for _ in range(frames):
                scene.push_pose()
                scene.render()
            ms = (time.perf_counter() - started) / frames * 1000
            row.append(f"{ms:7.1f} ms".rjust(12))
        print(" ".join(row), flush=True)

    print()
    print("30 fps needs 33 ms; 20 fps needs 50 ms. Pick a budget from the table and drive it.")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.view", description=__doc__)
    parser.add_argument("path", type=Path, help="a binary little-endian 3DGS .ply")
    parser.add_argument("--bench", action="store_true", help="measure frame cost and exit")
    parser.add_argument("--room-height", type=float, default=2.6, metavar="M",
                        help="floor to ceiling, metres. The one real number from outside")
    parser.add_argument("--flip", action="store_true",
                        help="the room came out upside down; override the density heuristic")
    parser.add_argument("--width", type=int, default=960)
    parser.add_argument("--height", type=int, default=720)
    args = parser.parse_args(argv)

    if not args.path.exists():
        print(f"no capture at {args.path}", file=sys.stderr)
        return 1

    splats, _room = prepare(args.path, room_height=args.room_height, flip=args.flip)

    if args.bench:
        bench(splats)
        return 0

    return Viewer(source=splats, width=args.width, height=args.height).run()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
