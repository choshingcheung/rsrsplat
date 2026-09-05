"""Configuration, and the rule that a credential never reaches an output stream."""

from __future__ import annotations

from pathlib import Path

import pytest

from marble.config import (
    KEY_VAR,
    MissingKey,
    api_key,
    capture_dir,
    describe_key,
    read_dotenv,
)

REAL_LOOKING = "wl_sk_live_9f2b7c1d4e8a0356f1b2c3d4e5f60718"


def test_a_dotenv_is_parsed_without_a_dependency(tmp_path: Path):
    env = tmp_path / ".env"
    env.write_text(
        "\n".join(
            [
                "# a comment",
                "",
                "WORLDLABS_API_KEY=abc123",
                'QUOTED="with quotes"',
                "SPACED  =  padded  ",
                "NOT_A_PAIR",
            ]
        ),
        encoding="utf-8",
    )

    values = read_dotenv(env)
    assert values["WORLDLABS_API_KEY"] == "abc123"
    assert values["QUOTED"] == "with quotes"
    assert values["SPACED"] == "padded"
    assert "NOT_A_PAIR" not in values


def test_a_missing_dotenv_is_not_an_error(tmp_path: Path):
    assert read_dotenv(tmp_path / "nothing-here") == {}


def test_the_environment_wins_over_the_dotenv(tmp_path: Path):
    env = tmp_path / ".env"
    env.write_text(f"{KEY_VAR}=from-file\n", encoding="utf-8")
    assert api_key({KEY_VAR: "from-env"}, dotenv=env) == "from-env"


def test_the_dotenv_is_the_fallback(tmp_path: Path):
    env = tmp_path / ".env"
    env.write_text(f"{KEY_VAR}=from-file\n", encoding="utf-8")
    assert api_key({}, dotenv=env) == "from-file"


def test_a_blank_key_counts_as_absent(tmp_path: Path):
    """An empty variable is the commonest way to have "set" a key and not have one."""
    env = tmp_path / ".env"
    env.write_text(f"{KEY_VAR}=\n", encoding="utf-8")
    with pytest.raises(MissingKey):
        api_key({KEY_VAR: "   "}, dotenv=env)


def test_the_missing_key_message_says_how_to_fix_it(tmp_path: Path):
    with pytest.raises(MissingKey) as caught:
        api_key({}, dotenv=tmp_path / "absent")

    message = str(caught.value)
    assert KEY_VAR in message
    assert ".env" in message


def test_describe_key_never_contains_the_key(tmp_path: Path):
    """PORTING.md hazard 5: the prototype interpolated a key into an error message.

    Anything that formats a credential eventually formats a real one into a log.
    """
    described = describe_key(REAL_LOOKING)

    assert REAL_LOOKING not in described
    assert described.endswith(f"({len(REAL_LOOKING)} chars)")
    # Enough of a prefix to tell two keys apart, not enough to be one.
    assert len(described.split("...")[0]) <= 6


def test_describe_key_does_not_leak_a_short_key():
    assert "secret" not in describe_key("secretsecret")


def test_describe_key_handles_no_key_at_all():
    assert describe_key("") == "no key"


def test_the_capture_dir_follows_the_projects_own_variable(tmp_path: Path):
    assert capture_dir({"SPLAT_CAPTURE_DIR": str(tmp_path)}) == tmp_path


def test_the_capture_dir_falls_back_without_creating_anything(tmp_path: Path):
    where = capture_dir({}, default=tmp_path / "out")
    assert where == tmp_path / "out"
    assert not where.exists()
