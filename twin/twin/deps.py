"""The two sibling repositories this track reads, and nothing else.

`twin` composes work that already exists in three places rather than reinventing any of it:

    akitech/splat/src      ground.py, splat_io.py -- deriving up, scale and the flat
                           surfaces of a capture. Ported to the browser as
                           web/src/scene/ground.ts; this is the Python original.
    akitech/app/sdk        akitech.cell -- the SO-101's kinematics, its joint conventions
                           and its IK. This is the "use akitech to control" half.
    robot_descriptions     the SO-101 MJCF itself, from mujoco_menagerie.

**Both repositories are read strictly read-only, over sys.path.** Nothing here writes to
them, imports from ``rsrsplat``'s own ``service/`` or ``web/``, or is imported by either.
See ``twin/README.md`` for why that matters while two sessions share this tree.

A missing repository fails here with the path it looked for, rather than as an
``ImportError`` three modules deep.
"""

from __future__ import annotations

import sys
from pathlib import Path

#: rsrsplat/twin/twin/deps.py -> twin -> rsrsplat -> the workspace holding both repos.
WORKSPACE = Path(__file__).resolve().parents[3]

SPLAT_SRC = WORKSPACE / "akitech" / "splat" / "src"
APP_SDK = WORKSPACE / "akitech" / "app" / "sdk"


class MissingRepo(RuntimeError):
    """A sibling repository is not where it is expected to be."""


def _require(path: Path, what: str, why: str) -> None:
    if not path.is_dir():
        raise MissingRepo(
            f"{what} is not at {path}.\n"
            f"twin needs it for {why}. Both repositories are expected as siblings of "
            f"rsrsplat in {WORKSPACE}."
        )
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))


def wire() -> None:
    """Put both repositories on the path. Idempotent, and safe to call from anywhere."""
    _require(SPLAT_SRC, "akitech/splat/src", "reading a capture and finding its floor")
    _require(APP_SDK, "akitech/app/sdk", "the SO-101's kinematics and IK")


def ground():
    """`akitech/splat`'s ground fitting: up, scale, floor, and the flat surfaces."""
    wire()
    import ground as module

    return module


def splat_io():
    """`akitech/splat`'s PLY reader."""
    wire()
    import splat_io as module

    return module


def kinematics():
    """`akitech/app`'s SO-101 kinematics, including IK."""
    wire()
    from akitech.cell import kinematics as module

    return module


__all__ = [
    "APP_SDK",
    "SPLAT_SRC",
    "WORKSPACE",
    "MissingRepo",
    "ground",
    "kinematics",
    "splat_io",
    "wire",
]
