"""The command line.

Every test here is offline. Where a command would need a key, the key lookup is replaced --
never left to find the real one, because a test that quietly reaches the live API is a test
that quietly spends money.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from conftest import splat_ply

from marble import __main__ as cli
from marble.config import MissingKey


@pytest.fixture(autouse=True)
def no_real_key(monkeypatch):
    """Nothing in this suite may reach the API, whatever is in the environment."""

    def refuse():
        raise AssertionError("a CLI test tried to authenticate against the live API")

    monkeypatch.setattr(cli, "api_key", refuse)


@pytest.fixture
def ply(tmp_path: Path) -> Path:
    path = tmp_path / "kitchen.ply"
    path.write_bytes(splat_ply(count=1000))
    return path


@pytest.fixture
def photo(tmp_path: Path) -> Path:
    path = tmp_path / "kitchen.jpg"
    path.write_bytes(b"\xff\xd8\xff")
    return path


# ---------------------------------------------------------------------------------------
# verify
# ---------------------------------------------------------------------------------------


def test_verify_reports_a_good_file(ply: Path, capsys):
    assert cli.main(["verify", str(ply)]) == 0
    out = capsys.readouterr().out
    assert "1,000 splats" in out
    assert "binary_little_endian" in out


def test_verify_on_a_missing_file_is_an_error(tmp_path: Path, capsys):
    assert cli.main(["verify", str(tmp_path / "absent.ply")]) == 1
    assert "no such file" in capsys.readouterr().err


def test_verify_on_something_that_is_not_a_ply_explains_itself(tmp_path: Path, capsys):
    junk = tmp_path / "expired.ply"
    junk.write_bytes(b"<?xml version='1.0'?><Error>ExpiredToken</Error>")

    assert cli.main(["verify", str(junk)]) == 1
    assert "not a PLY" in capsys.readouterr().err


# ---------------------------------------------------------------------------------------
# generate, and the money guard
# ---------------------------------------------------------------------------------------


def test_generate_needs_something_to_work_from(capsys):
    assert cli.main(["generate"]) == 1
    assert "--image" in capsys.readouterr().err


def test_generate_rejects_a_missing_image(tmp_path: Path, capsys):
    assert cli.main(["generate", "--image", str(tmp_path / "nope.jpg")]) == 1
    assert "no such image" in capsys.readouterr().err


def test_a_dry_run_prices_the_job_without_a_key_or_a_request(photo: Path, capsys):
    """The autouse fixture makes any authentication an outright failure, so this proves it."""
    assert cli.main(["generate", "--image", str(photo), "--dry-run"]) == 0

    out = capsys.readouterr().out
    assert "230 credits" in out
    assert "$0.18" in out
    assert "nothing was spent" in out


def test_a_dry_run_of_a_dearer_model_quotes_the_higher_price(photo: Path, capsys):
    assert cli.main(["generate", "--image", str(photo), "--model", "plus", "--dry-run"]) == 0
    assert "3,080 credits" in capsys.readouterr().out


def test_a_paid_model_is_refused_without_confirmation_in_a_script(photo: Path, capsys):
    """Non-interactive, no --yes: it must not spend $1.26 because nobody was watching."""
    assert cli.main(["generate", "--image", str(photo), "--model", "standard"]) == 1

    error = capsys.readouterr().err
    assert "--yes" in error
    assert "$1.26" in error


def test_several_images_are_priced_as_multi_image(photo: Path, tmp_path: Path, capsys):
    second = tmp_path / "b.jpg"
    second.write_bytes(b"jpeg-ish")

    assert cli.main(
        ["generate", "--image", str(photo), "--image", str(second), "--dry-run"]
    ) == 0
    out = capsys.readouterr().out
    assert "multi-image" in out
    assert "250 credits" in out


def test_an_assumed_orbit_is_announced_rather_than_silent(photo: Path, tmp_path: Path, capsys):
    """Even spacing is right for a set shot around a scene and wrong for a narrow arc.

    Either way the user must be able to see which was assumed.
    """
    second = tmp_path / "b.jpg"
    second.write_bytes(b"jpeg-ish")

    cli.main(["generate", "--image", str(photo), "--image", str(second), "--dry-run"])
    out = capsys.readouterr().out
    assert "assuming an even orbit" in out
    assert "0, 180" in out


def test_explicit_azimuths_replace_the_assumption(photo: Path, tmp_path: Path, capsys):
    second = tmp_path / "b.jpg"
    second.write_bytes(b"jpeg-ish")

    cli.main([
        "generate", "--image", str(photo), "--azimuth", "0",
        "--image", str(second), "--azimuth", "20", "--dry-run",
    ])
    assert "assuming an even orbit" not in capsys.readouterr().out


def test_mismatched_azimuths_are_refused(photo: Path, tmp_path: Path, capsys):
    second = tmp_path / "b.jpg"
    second.write_bytes(b"jpeg-ish")

    assert cli.main([
        "generate", "--image", str(photo), "--image", str(second),
        "--azimuth", "0", "--dry-run",
    ]) == 1
    assert "give one each" in capsys.readouterr().err


def test_a_missing_image_among_several_is_caught(photo: Path, tmp_path: Path, capsys):
    assert cli.main(
        ["generate", "--image", str(photo), "--image", str(tmp_path / "gone.jpg"), "--dry-run"]
    ) == 1
    assert "no such image" in capsys.readouterr().err


def test_the_default_model_is_the_cheap_one(photo: Path, capsys):
    cli.main(["generate", "--image", str(photo), "--dry-run"])
    assert "marble-1.0-draft" in capsys.readouterr().out


# ---------------------------------------------------------------------------------------
# status and resume
# ---------------------------------------------------------------------------------------


def test_status_on_an_empty_ledger_says_so(tmp_path: Path, capsys):
    assert cli.main(["--runs", str(tmp_path / "runs"), "status"]) == 0
    assert "no unfinished runs" in capsys.readouterr().out


def test_status_lists_what_is_outstanding(tmp_path: Path, capsys):
    from marble.run import Ledger

    ledger = Ledger(tmp_path / "runs")
    run = ledger.new(display_name="kitchen", model="marble-1.0-draft", model_alias="draft")
    run.update(status="generating", operation_id="op_gen_7c1d4b2a")

    assert cli.main(["--runs", str(ledger.directory), "status"]) == 0
    out = capsys.readouterr().out
    assert "generating" in out
    assert "marble resume" in out


def test_resuming_nothing_is_a_clear_error(tmp_path: Path, capsys):
    assert cli.main(["--runs", str(tmp_path / "runs"), "resume"]) == 1
    assert "no runs recorded" in capsys.readouterr().err


def test_a_run_with_nothing_paid_for_is_not_resumed(tmp_path: Path, capsys):
    from marble.run import Ledger

    ledger = Ledger(tmp_path / "runs")
    ledger.new(display_name="kitchen").fail("the key was rejected")

    assert cli.main(["--runs", str(ledger.directory), "resume"]) == 1
    assert "start a new run" in capsys.readouterr().err


def test_an_already_finished_run_is_left_alone(tmp_path: Path, capsys):
    from marble.run import Ledger

    ledger = Ledger(tmp_path / "runs")
    run = ledger.new(display_name="kitchen")
    run.update(status="verified", ply_path="out/kitchen.ply", world_id="w-1")

    assert cli.main(["--runs", str(ledger.directory), "resume"]) == 0
    assert "already finished" in capsys.readouterr().out


# ---------------------------------------------------------------------------------------
# Failure modes of the tool itself
# ---------------------------------------------------------------------------------------


def test_a_missing_key_is_reported_not_raised(monkeypatch, capsys):
    def missing():
        raise MissingKey("no WORLDLABS_API_KEY. Set it in the environment, or add .env")

    monkeypatch.setattr(cli, "api_key", missing)

    assert cli.main(["credits"]) == 1
    assert "WORLDLABS_API_KEY" in capsys.readouterr().err


def test_an_interrupted_poll_says_how_to_continue(monkeypatch, capsys):
    """Ctrl-C during a five minute wait is normal, and the world is still being built."""

    def interrupted(_args):
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "cmd_credits", interrupted)

    assert cli.main(["credits"]) == 130
    assert "marble resume" in capsys.readouterr().err
