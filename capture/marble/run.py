"""The run ledger: what was asked for, what it cost, and how far it got.

**Why this exists, and why it is built before the CLI.** A generation is about five minutes of
someone else's compute that has already been charged. Every other failure in this tool is free
to retry -- a bad argument, a rejected key, a truncated download. Losing the ``operation_id``
of a world that is already being built is the one failure that costs money to recover from,
and the way it happens is mundane: the terminal is closed, the laptop sleeps, the process is
killed while it is polling.

So the record is written to disk **before** the generate call is made, and updated the instant
an operation id comes back. After that, ``marble resume`` can pick up any run from any state.

The remaining window is genuinely irreducible: between the server accepting a generation and
the response reaching us, a crash leaves a paid world with no local record. ``marble worlds``
is the recovery for that -- it lists what the account actually has, which is the only source of
truth once the local one is gone.

Records are per-machine and gitignored. They hold world ids, prompts and local paths; no
credentials ever reach them.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass, field, fields
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

#: Where a run gets to. Ordered, so a status can be compared for "at least as far as".
STATUSES = (
    "created",  # written before anything was spent
    "generating",  # the API accepted it; an operation id exists
    "generated",  # the world is built
    "exporting",  # a PLY has been asked for
    "downloaded",  # the file is on disk
    "verified",  # and it parses as 3DGS
    "failed",
)

DEFAULT_DIR = Path(__file__).resolve().parents[1] / "runs"


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def slugify(text: str, fallback: str = "world") -> str:
    """A short, filesystem-safe stem. Windows-safe by construction, not by hope."""
    cleaned = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return (cleaned[:40].rstrip("-") or fallback)


@dataclass
class Run:
    """One capture attempt, from the sentence that started it to the file it produced."""

    id: str
    created_at: str
    updated_at: str
    status: str = "created"

    # What was asked for.
    display_name: str = ""
    model: str = ""
    model_alias: str = ""
    prompt_kind: str = "text"
    prompt_text: str | None = None
    image_path: str | None = None

    # What it was expected to cost. Recorded before the spend, so a surprise is visible.
    estimated_credits: int = 0

    # What came back.
    operation_id: str | None = None
    world_id: str | None = None
    export_operation_id: str | None = None
    ply_path: str | None = None
    sidecar_path: str | None = None
    splat_count: int | None = None
    error: str | None = None

    #: Not persisted; set by the ledger so a run can save itself.
    _dir: Path | None = field(default=None, repr=False, compare=False)

    # -- persistence ---------------------------------------------------------------------

    @property
    def path(self) -> Path:
        if self._dir is None:
            raise RuntimeError("this run is not attached to a ledger")
        return self._dir / f"{self.id}.json"

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("_dir", None)
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any], directory: Path | None = None) -> Run:
        known = {f.name for f in fields(cls)} - {"_dir"}
        # Unknown keys are dropped rather than raising: a record written by a later version
        # of this tool should still be resumable by an earlier one.
        run = cls(**{k: v for k, v in data.items() if k in known})
        run._dir = directory
        return run

    def save(self) -> Run:
        """Write atomically. A half-written ledger entry is worse than none at all."""
        self.updated_at = _now()
        self.path.parent.mkdir(parents=True, exist_ok=True)

        temporary = self.path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(self.to_dict(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        os.replace(temporary, self.path)
        return self

    def update(self, **changes: Any) -> Run:
        """Set fields and persist in one step, so no caller can forget the second half."""
        for name, value in changes.items():
            if not hasattr(self, name):
                raise AttributeError(f"Run has no field {name!r}")
            setattr(self, name, value)
        return self.save()

    def fail(self, reason: str) -> Run:
        return self.update(status="failed", error=str(reason)[:2000])

    # -- questions the CLI asks -----------------------------------------------------------

    @property
    def reached(self) -> int:
        return STATUSES.index(self.status) if self.status in STATUSES else -1

    @property
    def finished(self) -> bool:
        return self.status in ("verified", "failed")

    @property
    def resumable(self) -> bool:
        """Anything with a world or an operation is worth picking up rather than repeating.

        A failed run is included on purpose: the failure is usually the download or the
        export, both of which are free to retry against a world that already exists.
        """
        return bool(self.operation_id or self.world_id) and self.status != "verified"

    def describe(self) -> str:
        bits = [f"{self.id}  {self.status:<11}"]
        if self.world_id:
            bits.append(f"world {self.world_id[:8]}")
        elif self.operation_id:
            bits.append(f"op {self.operation_id[:12]}")
        bits.append(self.display_name or self.prompt_text or "")
        if self.error:
            bits.append(f"-- {self.error[:60]}")
        return "  ".join(b for b in bits if b)


@dataclass
class Ledger:
    """Every run this machine has started."""

    directory: Path = DEFAULT_DIR

    def new(self, *, display_name: str, **details: Any) -> Run:
        """Create and immediately persist a run. Called BEFORE anything is spent."""
        stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        run = Run(
            id=f"{stamp}-{slugify(display_name)}",
            created_at=_now(),
            updated_at=_now(),
            display_name=display_name,
            **details,
        )
        run._dir = self.directory
        return run.save()

    def load(self, run_id: str) -> Run:
        path = self.directory / f"{run_id}.json"
        if not path.is_file():
            raise FileNotFoundError(f"no run {run_id!r} in {self.directory}")
        return Run.from_dict(json.loads(path.read_text(encoding="utf-8")), self.directory)

    def all(self) -> list[Run]:
        """Newest first. Unreadable records are skipped, not fatal."""
        runs: list[Run] = []
        if not self.directory.is_dir():
            return runs

        for path in sorted(self.directory.glob("*.json"), reverse=True):
            try:
                runs.append(
                    Run.from_dict(json.loads(path.read_text(encoding="utf-8")), self.directory)
                )
            except (json.JSONDecodeError, TypeError, ValueError):
                continue
        return runs

    def latest(self) -> Run | None:
        runs = self.all()
        return runs[0] if runs else None

    def resolve(self, reference: str | None) -> Run:
        """A run id, or ``latest``, or nothing at all meaning the most recent."""
        if reference in (None, "", "latest", "last"):
            run = self.latest()
            if run is None:
                raise FileNotFoundError(f"no runs recorded in {self.directory}")
            return run
        return self.load(str(reference))

    def unfinished(self) -> list[Run]:
        return [run for run in self.all() if not run.finished]


__all__ = ["DEFAULT_DIR", "STATUSES", "Ledger", "Run", "slugify"]
