"""One capture, from a sentence to a verified file.

The whole pipeline is a state machine over a :class:`~marble.run.Run`, and :func:`advance`
moves a run forward from wherever it currently is:

    created -> generating -> generated -> exporting -> downloaded -> verified

``generate`` and ``resume`` are the same code path. That is deliberate and it is the only way
resumability is real: a resume that runs different code from the original attempt is a second
implementation that is exercised only when something has already gone wrong.

Every transition is persisted as it happens, so killing the process at any point leaves a run
that can be picked up rather than paid for again.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from . import verify as ply
from .client import (
    Marble,
    MarbleError,
    Model,
    estimate_credits,
    image_prompt,
    multi_image_prompt,
    semantics_of,
    text_prompt,
)
from .run import Ledger, Run

#: Written into every sidecar, so a file found later can be traced back to a version.
TOOL = "rsrsplat-capture"
VERSION = "0.1.0"

Say = Callable[[str], None]


def _quiet(_message: str) -> None:
    pass


class NotEnoughCredits(MarbleError):
    """Refused before spending, rather than discovered halfway through."""


def build_prompt(
    text: str | None, asset_ids: Sequence[str], azimuths: Sequence[float]
) -> dict[str, Any]:
    """The world_prompt for this run, from however many images were uploaded.

    One image and a sentence is an image prompt with the text refining it. Two or more is a
    multi-image prompt, where each view carries the angle it was taken from.
    """
    if len(asset_ids) > 1:
        return multi_image_prompt(list(zip(asset_ids, azimuths, strict=True)), text=text)
    if asset_ids:
        return image_prompt(media_asset_id=asset_ids[0], text=text)
    if not text:
        raise ValueError("give an image, a text prompt, or both")
    return text_prompt(text)


def price(text: str | None, images: Sequence[Path], model: Model) -> int:
    """Worst-case credits, without contacting anything. Used by ``--dry-run``.

    Multi-image costs more panorama than a single image -- 100 credits against 80 -- so the
    count changes the quote and not only the upload time.
    """
    kind = "multi-image" if len(images) > 1 else "image" if images else "text"
    return estimate_credits({"type": kind, "text_prompt": text}, model)


def start(
    client: Marble,
    ledger: Ledger,
    *,
    text: str | None,
    images: Sequence[Path],
    azimuths: Sequence[float],
    model: Model,
    model_alias: str,
    display_name: str,
    check_credits: bool = True,
    say: Say = _quiet,
) -> Run:
    """Record the intent, spend the credits, and record what came back.

    The ledger entry is written **before** the generate call, so the window in which a crash
    can lose a paid world is only as wide as one HTTP round trip. ``marble worlds`` is the
    recovery for that window; nothing can close it entirely from this side.
    """
    estimated = price(text, images, model)

    if check_credits:
        remaining = client.credits()
        say(f"credits: {remaining:,} remaining, this run needs about {estimated:,}")
        if remaining < estimated:
            raise NotEnoughCredits(
                f"this run needs about {estimated:,} credits and the account has "
                f"{remaining:,}. Top up at https://marble.worldlabs.ai/ -- minimum $5.00."
            )

    run = ledger.new(
        display_name=display_name,
        model=model.name,
        model_alias=model_alias,
        prompt_kind=("multi-image" if len(images) > 1 else "image" if images else "text"),
        prompt_text=text,
        image_paths=[str(p) for p in images],
        azimuths=list(azimuths),
        estimated_credits=estimated,
    )
    say(f"run {run.id} recorded before spending anything")

    try:
        asset_ids: list[str] = []
        for image, azimuth in zip(images, azimuths, strict=True):
            size = image.stat().st_size / 1e6
            at = f" at {azimuth:g} deg" if len(images) > 1 else ""
            say(f"uploading {image.name} ({size:.1f} MB){at}")
            asset_ids.append(client.upload_file(image))

        prompt = build_prompt(text, asset_ids, azimuths)
        say(f"generating with {model.name}")
        operation = client.generate(prompt, model, display_name=display_name)
    except Exception as exc:
        run.fail(str(exc))
        raise

    # The first thing done with the response, before anything can go wrong with it.
    run.update(
        status="generating",
        operation_id=operation.operation_id,
        world_id=operation.world_id,
    )
    return run


def advance(
    client: Marble,
    run: Run,
    *,
    out_dir: Path,
    say: Say = _quiet,
    poll_interval: float = 5.0,
    timeout: float = 1800.0,
) -> Run:
    """Drive a run to ``verified`` from whatever state it is in. Safe to call repeatedly."""
    try:
        if run.status in ("created", "generating"):
            run = _wait_for_world(client, run, say=say, interval=poll_interval, timeout=timeout)
        if run.status in ("generated", "exporting"):
            run = _download(client, run, out_dir=out_dir, say=say, interval=poll_interval)
        if run.status == "downloaded":
            run = _finish(client, run, say=say)
    except Exception as exc:
        run.fail(str(exc))
        raise
    return run


def _wait_for_world(
    client: Marble, run: Run, *, say: Say, interval: float, timeout: float
) -> Run:
    if not run.operation_id:
        raise MarbleError(
            f"run {run.id} has no operation id, so there is nothing to wait for. "
            f"Check `marble worlds` for a world this run may have paid for."
        )

    # Measured: a draft world takes 21-26 seconds. The documented five minutes is presumably
    # the larger models, which are untested here -- so say both rather than either.
    say(f"waiting for {run.operation_id} (draft takes about half a minute)")

    seen_world = run.world_id

    def progress(operation: Any, elapsed: float) -> None:
        nonlocal seen_world
        say(f"  {elapsed:6.0f}s  {operation.status}")
        # Persist the world id the moment it appears: from here the generation is
        # recoverable even if this process never returns.
        if operation.world_id and operation.world_id != seen_world:
            seen_world = operation.world_id
            run.update(world_id=operation.world_id)

    operation = client.poll(
        run.operation_id, interval=interval, timeout=timeout, on_progress=progress
    )
    world_id = operation.world_id or run.world_id
    if not world_id:
        raise MarbleError("the generation finished but reported no world id")

    return run.update(status="generated", world_id=world_id)


def _download(client: Marble, run: Run, *, out_dir: Path, say: Say, interval: float) -> Run:
    if not run.world_id:
        raise MarbleError(f"run {run.id} has no world to export")

    run.update(status="exporting")
    say(f"exporting world {run.world_id} as PLY (free, usually cached)")
    url = client.export_url(run.world_id, interval=interval)

    out_dir.mkdir(parents=True, exist_ok=True)
    destination = out_dir / f"{run.id}.ply"

    last = -1.0

    def progress(written: int, total: int | None) -> None:
        nonlocal last
        megabytes = written / 1e6
        if megabytes - last < 25:  # a line every 25 MB, not every chunk
            return
        last = megabytes
        if total:
            say(f"  {megabytes:7.1f} MB of {total / 1e6:.1f} MB")
        else:
            say(f"  {megabytes:7.1f} MB")

    say(f"downloading to {destination}")
    written = client.download(url, destination, on_progress=progress)
    say(f"  {written / 1e6:.1f} MB written")

    return run.update(status="downloaded", ply_path=str(destination))


def _finish(client: Marble, run: Run, *, say: Say) -> Run:
    """Verify the file, fetch what Marble knows about the world, write the sidecar."""
    path = Path(run.ply_path or "")
    info = ply.verify(path)
    say(f"verified: {info.summary()}")

    world: dict[str, Any] = {}
    try:
        world = client.world(run.world_id or "")
    except MarbleError as exc:
        # A missing sidecar is a nuisance; a lost capture is not. The file is already on
        # disk and verified, so this must not fail the run.
        say(f"could not fetch world metadata ({exc}); the sidecar will be thinner")

    sidecar = write_sidecar(run, world, info)
    say(f"sidecar: {sidecar.name}")

    return run.update(
        status="verified", splat_count=info.count, sidecar_path=str(sidecar)
    )


def sidecar_for(ply_path: Path) -> Path:
    return ply_path.with_suffix(".marble.json")


def write_sidecar(run: Run, world: dict[str, Any], info: ply.PlyInfo) -> Path:
    """What Marble knows about this capture, next to the capture.

    The interesting half is ``semantics``. Marble reports a metric scale factor and a ground
    plane offset for every world -- which is an independent answer to what rsrsplat's browser
    derives by RANSAC and layer scoring. Recorded here as data, for comparison. Nothing in
    this tool applies it, and nothing in rsrsplat is asked to.
    """
    ply_path = Path(run.ply_path or "")
    path = sidecar_for(ply_path)
    semantics = semantics_of(world)

    document = {
        "tool": TOOL,
        "version": VERSION,
        "run_id": run.id,
        "created_at": run.created_at,
        "world": {
            "id": run.world_id,
            "display_name": world.get("display_name") or run.display_name,
            "model": world.get("model") or run.model,
            "url": world.get("world_marble_url"),
        },
        "prompt": {
            "kind": run.prompt_kind,
            "text": run.prompt_text,
            "images": [Path(p).name for p in run.image_paths] or None,
            "azimuths": list(run.azimuths) or None,
        },
        "ply": {
            "file": ply_path.name,
            "bytes": info.file_bytes,
            "splat_count": info.count,
            "sh_degree": info.sh_degree,
            "properties": len(info.properties),
        },
        "semantics": {
            **semantics,
            "reported": bool(semantics),
            "how_to_apply": (
                "When metric_scale_factor is present, multiply positions and scales by it to "
                "get metres, then subtract ground_plane_offset from the vertical axis to put "
                "the floor at zero. When 'reported' is false, Marble supplied neither and the "
                "capture's scale is unknown -- fit it from the cloud."
            ),
            "axes": (
                "MEASURED on two worlds, one text-derived and one from a photograph, not "
                "assumed: the vertical axis is Y and +Y is UP, with the floor at MINIMUM y. "
                "Established by looking for the floor-and-ceiling pair of sharp density peaks "
                "on percentile-trimmed data, which is the test ground.ts itself uses. Note "
                "this contradicts CLAUDE.md's 'Marble is OpenCV, +y down'. The transform that "
                "lands it Z-up with the floor at minimum z is service/app/splat/transforms.py "
                "'opencv_to_zup' (new_z = old_y); 'yup_to_zup' inverts it and puts the floor "
                "overhead. See capture/NOTES.md."
            ),
        },
        "note": (
            "Written by rsrsplat-capture. Nothing in web/ or service/ reads this file; it is "
            "here so a capture can be traced, and so the fitted ground plane has something "
            "independent to be checked against."
        ),
    }

    path.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


__all__ = [
    "TOOL",
    "VERSION",
    "NotEnoughCredits",
    "advance",
    "build_prompt",
    "price",
    "sidecar_for",
    "start",
    "write_sidecar",
]
