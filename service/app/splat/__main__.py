"""Inspect a capture before trusting it.

    python -m app.splat <capture.ply> [--transform opencv_to_zup] [--header-only]

Prints Gaussian count, SH degree, bounding box and extent. The number to look at is the
longest axis of the extent: if it is not a plausible number of metres for the space that was
scanned, the capture is not metric and everything downstream that assumes metres is wrong.

This is also the reference the browser's own parser is checked against — the counts must
agree exactly.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import transforms
from .ply import load, sh_degree, splat_count


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.splat", description=__doc__)
    parser.add_argument("path", type=Path, help="a binary little-endian 3DGS PLY")
    parser.add_argument(
        "--transform",
        default="identity",
        choices=sorted(transforms.TRANSFORMS),
        help="coordinate conversion to apply before reporting (default: identity)",
    )
    parser.add_argument(
        "--scale",
        type=float,
        default=1.0,
        help="isotropic rescale applied with the transform, e.g. to convert to metres",
    )
    parser.add_argument(
        "--header-only",
        action="store_true",
        help="read just the header: count and SH degree, without parsing the body",
    )
    args = parser.parse_args(argv)

    if not args.path.exists():
        print(f"no such file: {args.path}", file=sys.stderr)
        return 2

    try:
        if args.header_only:
            print(f"splats      {splat_count(args.path):,}")
            degree = sh_degree(args.path)
            print(f"sh degree   {degree if degree is not None else 'unknown'}")
            return 0

        splats = load(args.path)
        if args.transform != "identity" or args.scale != 1.0:
            splats = transforms.apply(splats, args.transform, args.scale)
        print(f"file        {args.path}")
        print(f"transform   {args.transform}" + (f" x{args.scale:g}" if args.scale != 1.0 else ""))
        print(splats.report())
    except (ValueError, KeyError, OSError) as exc:
        print(f"{args.path}: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
