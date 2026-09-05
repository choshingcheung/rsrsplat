"""The command line.

    marble credits                                  what is in the account
    marble generate --image kitchen.jpg             a world from a photograph
    marble generate --text "a utility room"         a world from a sentence
    marble generate --image k.jpg --dry-run         what it would cost, spending nothing
    marble resume [run-id]                          pick a run back up
    marble status                                   what has been started here
    marble worlds                                   what the account actually holds
    marble verify path/to/file.ply                  is that a splat cloud

**The money rules, in one place.** Draft is the default model. A dearer one needs ``--yes``,
or a terminal to confirm at -- never silently, and never in a script. The balance is checked
before a run starts. ``--dry-run`` prices a run without touching anything that costs.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import capture as pipeline
from . import verify as ply
from .client import DEFAULT_MODEL, MODELS, Marble, MarbleError, usd, world_id_of
from .config import MissingKey, api_key, capture_dir, describe_key
from .run import DEFAULT_DIR, Ledger


def say(message: str) -> None:
    print(message, flush=True)


def fail(message: str) -> int:
    print(f"error: {message}", file=sys.stderr, flush=True)
    return 1


def client_for(args: argparse.Namespace) -> Marble:
    key = api_key()
    if getattr(args, "verbose", False):
        say(f"key: {describe_key(key)}")
    return Marble(api_key=key)


def ledger_for(args: argparse.Namespace) -> Ledger:
    return Ledger(Path(args.runs) if getattr(args, "runs", None) else DEFAULT_DIR)


# ---------------------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------------------


def cmd_credits(args: argparse.Namespace) -> int:
    remaining = client_for(args).credits()
    say(f"{remaining:,} credits (about ${usd(remaining):.2f})")
    for alias, model in MODELS.items():
        runs = remaining // (model.worst_case + 80)
        say(f"  {alias:<9} {model.name:<20} ~{runs:,} more worlds")
    return 0


def cmd_generate(args: argparse.Namespace) -> int:
    if not args.image and not args.text:
        return fail("give --image, --text, or both")

    image = Path(args.image).expanduser() if args.image else None
    if image is not None and not image.is_file():
        return fail(f"no such image: {image}")

    model = MODELS[args.model]
    estimated = pipeline.price(args.text, image, model)
    name = args.name or (image.stem if image else (args.text or "world")[:40])

    say(f"{name}: {model.name}, about {estimated:,} credits (${usd(estimated):.2f})")

    if args.dry_run:
        say("dry run: nothing was sent and nothing was spent")
        return 0

    # A dearer model is never started without a person saying so. In a script, that means
    # --yes; at a terminal, an actual question.
    if args.model != DEFAULT_MODEL and not args.yes:
        if not sys.stdin.isatty():
            return fail(
                f"--model {args.model} costs about ${usd(estimated):.2f} per run. "
                f"Pass --yes to confirm it in a non-interactive session."
            )
        answer = input(f"spend about ${usd(estimated):.2f} on {model.name}? [y/N] ").strip()
        if answer.lower() not in ("y", "yes"):
            return fail("cancelled; nothing was spent")

    client = client_for(args)
    ledger = ledger_for(args)

    run = pipeline.start(
        client,
        ledger,
        text=args.text,
        image=image,
        model=model,
        model_alias=args.model,
        display_name=name,
        say=say,
    )
    run = pipeline.advance(
        client, run, out_dir=Path(args.out) if args.out else capture_dir(), say=say
    )

    say("")
    say(f"done: {run.ply_path}")
    say(f"      {run.sidecar_path}")
    return 0


def cmd_resume(args: argparse.Namespace) -> int:
    ledger = ledger_for(args)
    run = ledger.resolve(args.run)

    if run.status == "verified":
        say(f"{run.id} is already finished: {run.ply_path}")
        return 0
    if not run.resumable:
        return fail(
            f"{run.id} has no world and no operation to resume "
            f"({run.status}{f': {run.error}' if run.error else ''}). "
            f"Nothing was paid for, so start a new run."
        )

    say(f"resuming {run.id} from {run.status}")
    run = pipeline.advance(
        client_for(args), run, out_dir=Path(args.out) if args.out else capture_dir(), say=say
    )
    say(f"done: {run.ply_path}")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    ledger = ledger_for(args)
    runs = ledger.all() if args.all else ledger.unfinished()

    if not runs:
        say("no unfinished runs" if not args.all else f"no runs in {ledger.directory}")
        return 0

    for run in runs:
        say(run.describe())
    if not args.all:
        say("")
        say("`marble status --all` for finished runs, `marble resume <id>` to continue one.")
    return 0


def cmd_worlds(args: argparse.Namespace) -> int:
    """What the account holds, which is the truth when a local record was lost."""
    worlds = client_for(args).worlds(limit=args.limit)
    if not worlds:
        say("no worlds on this account")
        return 0

    for world in worlds:
        # The id comes back as `world_id`, not `id`; reading only `id` printed a blank column.
        say(
            f"{(world_id_of(world) or '?')[:8]}  {str(world.get('model', '')):<20} "
            f"{world.get('created_at', ''):<22} {world.get('display_name', '')}"
        )
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    path = Path(args.path).expanduser()
    if not path.is_file():
        return fail(f"no such file: {path}")

    info = ply.verify(path)
    say(f"{path.name}: {info.summary()}")
    say(f"  format          {info.format}")
    say(f"  header          {info.header_bytes} bytes")
    say(f"  per vertex      {info.vertex_bytes} bytes")
    say(f"  expected size   {info.expected_bytes:,} bytes")
    return 0


# ---------------------------------------------------------------------------------------
# Wiring
# ---------------------------------------------------------------------------------------


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(
        prog="marble",
        description="Turn an image or a prompt into a Gaussian splat capture.",
    )
    root.add_argument("-v", "--verbose", action="store_true", help="report the key in use")
    root.add_argument("--runs", help=f"run ledger directory (default {DEFAULT_DIR})")
    sub = root.add_subparsers(dest="command", required=True)

    generate = sub.add_parser("generate", help="generate a world and download it as a PLY")
    generate.add_argument("--image", help="photograph or panorama to build the world from")
    generate.add_argument("--text", help="a description, alone or alongside an image")
    generate.add_argument(
        "--model",
        choices=sorted(MODELS),
        default=DEFAULT_MODEL,
        help=f"default {DEFAULT_MODEL}, the cheapest",
    )
    generate.add_argument("--name", help="display name; defaults to the image stem")
    generate.add_argument("--out", help="where the .ply lands (default SPLAT_CAPTURE_DIR)")
    generate.add_argument("--dry-run", action="store_true", help="price it and stop")
    generate.add_argument("--yes", action="store_true", help="confirm a paid model")
    generate.set_defaults(handler=cmd_generate)

    resume = sub.add_parser("resume", help="continue a run that was interrupted")
    resume.add_argument("run", nargs="?", help="run id, or the most recent by default")
    resume.add_argument("--out", help="where the .ply lands")
    resume.set_defaults(handler=cmd_resume)

    status = sub.add_parser("status", help="what has been started on this machine")
    status.add_argument("--all", action="store_true", help="include finished runs")
    status.set_defaults(handler=cmd_status)

    worlds = sub.add_parser("worlds", help="what the account holds, per the API")
    worlds.add_argument("--limit", type=int, default=20)
    worlds.set_defaults(handler=cmd_worlds)

    verify = sub.add_parser("verify", help="check a .ply is a Gaussian splat cloud")
    verify.add_argument("path")
    verify.set_defaults(handler=cmd_verify)

    sub.add_parser("credits", help="remaining API credits").set_defaults(handler=cmd_credits)
    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        return int(args.handler(args))
    except MissingKey as exc:
        return fail(str(exc))
    except (MarbleError, ply.VerifyError) as exc:
        return fail(str(exc))
    except FileNotFoundError as exc:
        return fail(str(exc))
    except KeyboardInterrupt:
        # Interrupting a poll is normal and must not read like a crash: the world is still
        # being built and the ledger already knows how to pick it up.
        print("\ninterrupted. `marble resume` continues where this left off.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
