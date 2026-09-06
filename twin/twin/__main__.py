"""The command line.

    twin inspect <capture.ply>     what the scan says: up, scale, floor, surfaces
    twin run     <capture.ply>     build it and pick the block up, headless
    twin view    <capture.ply>     the same, in the MuJoCo viewer

    --flip            invert up, when the floor came out overhead
    --surface N       stand on surface N from `inspect` rather than the best one
    --room-height M   the ceiling assumption the whole scale rests on (default 2.6)
    --no-walls        leave the walls out, for a clearer view

**`inspect` first, always.** Scale and which-way-is-up are both derived, and a capture that
came out upside down puts the arm on the ceiling. The viewer is the honest check and costs
nothing to run.
"""

from __future__ import annotations

import argparse
import sys

import mujoco

from . import pick, room, scene
from .deps import MissingRepo


def say(message: str) -> None:
    print(message, flush=True)


def fail(message: str) -> int:
    print(f"error: {message}", file=sys.stderr, flush=True)
    return 1


def load(args: argparse.Namespace) -> room.Room:
    return room.read(args.capture, room_height_m=args.room_height, flip=args.flip)


def chosen(scanned: room.Room, index: int | None) -> room.Surface | None:
    if index is None:
        return None
    ordered = sorted(scanned.surfaces, key=lambda s: s.height)
    if not 0 <= index < len(ordered):
        raise IndexError(f"no surface {index}; there are {len(ordered)}, numbered from 0")
    return ordered[index]


def compose(args: argparse.Namespace):
    scanned = load(args)
    say(scanned.report())

    placement = scene.place(scanned, chosen(scanned, args.surface))
    say("")
    say(placement.describe())

    model = scene.build(scanned, placement, walls=not args.no_walls)
    data = mujoco.MjData(model)
    mujoco.mj_forward(model, data)
    scene.settle(model, data, 0.5)
    return scanned, placement, model, data


# ---------------------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------------------


def cmd_inspect(args: argparse.Namespace) -> int:
    scanned = load(args)
    say(scanned.report())
    say("")
    say("surfaces, numbered for --surface:")
    for i, s in enumerate(sorted(scanned.surfaces, key=lambda x: x.height)):
        say(f"  [{i}] {s.describe()}")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    _, placement, model, data = compose(args)
    say("")
    result = pick.run(model, data, placement)
    say(result.describe())
    return 0 if result.ok else 1


def cmd_view(args: argparse.Namespace) -> int:
    try:
        from mujoco import viewer
    except ImportError:
        return fail("this build of mujoco has no viewer; use `twin run` instead")

    _, placement, model, data = compose(args)
    say("")
    say("opening the viewer -- drag to orbit. The pick starts once the window is up.")

    with viewer.launch_passive(model, data) as window:
        result = pick.run(model, data, placement, on_tick=window.sync)
        say(result.describe())
        say("close the window to finish.")
        while window.is_running():
            mujoco.mj_step(model, data)
            window.sync()
    return 0


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="twin", description=__doc__.split("\n")[0])
    sub = root.add_subparsers(dest="command", required=True)

    for name, handler, help_text in (
        ("inspect", cmd_inspect, "report up, scale, floor and surfaces"),
        ("run", cmd_run, "build the scene and pick the block up, headless"),
        ("view", cmd_view, "the same, in the MuJoCo viewer"),
    ):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("capture", help="a 3DGS .ply")
        p.add_argument("--flip", action="store_true", help="invert up")
        p.add_argument("--surface", type=int, help="stand on this surface, from inspect")
        p.add_argument("--room-height", type=float, default=room.ROOM_HEIGHT_M)
        p.add_argument("--no-walls", action="store_true")
        p.set_defaults(handler=handler)

    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        return int(args.handler(args))
    except (MissingRepo, FileNotFoundError, IndexError) as exc:
        return fail(str(exc))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
