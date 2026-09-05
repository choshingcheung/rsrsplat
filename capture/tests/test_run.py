"""The ledger, and the crash it exists to survive."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from marble.run import Ledger, Run, slugify


@pytest.fixture
def ledger(tmp_path: Path) -> Ledger:
    return Ledger(tmp_path / "runs")


def start(ledger: Ledger, name: str = "kitchen", **extra) -> Run:
    return ledger.new(
        display_name=name,
        model="marble-1.0-draft",
        model_alias="draft",
        prompt_kind="image",
        prompt_text="a domestic kitchen",
        image_path="photos/kitchen.jpg",
        estimated_credits=230,
        **extra,
    )


def test_a_run_is_on_disk_before_anything_is_spent(ledger: Ledger):
    """The record is written first, so the crash window is as small as it can be."""
    run = start(ledger)

    assert run.path.is_file()
    assert run.status == "created"
    assert run.operation_id is None
    assert run.estimated_credits == 230


def test_an_operation_id_survives_the_process(ledger: Ledger, tmp_path: Path):
    """The whole point: a killed poll is resumable rather than paid for twice."""
    run = start(ledger)
    run.update(status="generating", operation_id="op_gen_7c1d4b2a", world_id="w-1")

    # A completely fresh ledger, as a new process would see it.
    reopened = Ledger(tmp_path / "runs").load(run.id)

    assert reopened.operation_id == "op_gen_7c1d4b2a"
    assert reopened.world_id == "w-1"
    assert reopened.status == "generating"
    assert reopened.resumable


def test_update_persists_without_a_second_call(ledger: Ledger):
    run = start(ledger)
    run.update(status="generated")
    assert json.loads(run.path.read_text(encoding="utf-8"))["status"] == "generated"


def test_update_rejects_a_field_that_does_not_exist(ledger: Ledger):
    """A typo'd field name would otherwise be silently written and silently lost."""
    run = start(ledger)
    with pytest.raises(AttributeError):
        run.update(wrold_id="w-1")


def test_a_record_is_never_left_half_written(ledger: Ledger):
    run = start(ledger)
    run.update(status="downloaded", ply_path="out/kitchen.ply")

    assert list(run.path.parent.glob("*.tmp")) == []
    json.loads(run.path.read_text(encoding="utf-8"))  # parses, therefore complete


def test_a_record_from_a_later_version_still_loads(ledger: Ledger):
    """Forward compatibility, so an old build can still resume a run and free the world."""
    run = start(ledger)
    data = json.loads(run.path.read_text(encoding="utf-8"))
    data["some_future_field"] = {"added": "later"}
    run.path.write_text(json.dumps(data), encoding="utf-8")

    assert ledger.load(run.id).display_name == "kitchen"


def stamped(ledger: Ledger, run_id: str, name: str) -> Run:
    """A run with a chosen id, so ordering can be asserted without waiting a second."""
    run = Run(id=run_id, created_at="2026-01-01T00:00:00+00:00", updated_at="", display_name=name)
    run._dir = ledger.directory
    return run.save()


def test_runs_come_back_newest_first(ledger: Ledger):
    stamped(ledger, "20260101-120000-one", "one")
    stamped(ledger, "20260202-120000-two", "two")

    assert [r.id for r in ledger.all()] == ["20260202-120000-two", "20260101-120000-one"]
    assert ledger.latest().id == "20260202-120000-two"


def test_an_unreadable_record_does_not_take_the_listing_down(ledger: Ledger):
    start(ledger, "good")
    ledger.directory.mkdir(parents=True, exist_ok=True)
    (ledger.directory / "20260303-120000-corrupt.json").write_text("{ not json", encoding="utf-8")

    assert len(ledger.all()) == 1


def test_latest_resolves_when_no_run_is_named(ledger: Ledger):
    run = start(ledger)
    assert ledger.resolve(None).id == run.id
    assert ledger.resolve("latest").id == run.id


def test_resolving_a_missing_run_says_so(ledger: Ledger):
    with pytest.raises(FileNotFoundError):
        ledger.resolve("20260101-000000-nope")


def test_an_empty_ledger_resolves_to_a_clear_error(ledger: Ledger):
    with pytest.raises(FileNotFoundError, match="no runs recorded"):
        ledger.resolve(None)


def test_a_verified_run_is_finished_and_not_resumable(ledger: Ledger):
    run = start(ledger)
    run.update(status="verified", world_id="w-1", operation_id="op-1")
    assert run.finished
    assert not run.resumable


def test_a_failed_run_with_a_world_is_still_worth_resuming(ledger: Ledger):
    """The failure is usually the download. The world is built and already paid for."""
    run = start(ledger)
    run.update(status="generated", world_id="w-1")
    run.fail("download truncated")

    assert run.finished
    assert run.resumable
    assert run.error == "download truncated"


def test_a_run_with_nothing_to_resume_says_so(ledger: Ledger):
    run = start(ledger)
    run.fail("the key was rejected")
    assert not run.resumable


def test_unfinished_lists_what_is_outstanding(ledger: Ledger):
    done = start(ledger, "done")
    done.update(status="verified")
    running = start(ledger, "running")
    running.update(status="generating", operation_id="op-2")

    assert [r.display_name for r in ledger.unfinished()] == ["running"]


def test_a_slug_is_safe_on_windows():
    assert slugify("A Kitchen: 90 degrees / hinged!") == "a-kitchen-90-degrees-hinged"
    assert slugify("") == "world"
    assert slugify("...") == "world"
    assert len(slugify("x" * 200)) <= 40


def test_describe_is_readable_at_a_terminal(ledger: Ledger):
    run = start(ledger)
    run.update(status="generating", world_id="9f1c2a44-0c3f-4a2e")
    assert "generating" in run.describe()
    assert "9f1c2a44" in run.describe()
