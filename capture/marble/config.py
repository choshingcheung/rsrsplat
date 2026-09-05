"""Where the key and the output directory come from.

**The key is never printed, logged, or interpolated into an error.** ``PORTING.md`` hazard 5
records the prototype doing exactly that: a message that read

    ANTHROPIC_API_KEY, but it is a placeholder ('sk-ant-xxx') -- fill it in or unset it

which puts a credential in a log the moment someone mistypes one. So :func:`describe_key`
reports a length and a short prefix and there is no code path anywhere that formats the value.

The key is read from the environment first, then from the repo-root ``.env``. Root ``.env`` is
gitignored, which is the whole reason this tool reads it rather than adding a line to
``.env.example`` -- an ignored file cannot produce a merge conflict with the session working
in the other half of the repo. See ``capture/README.md``.
"""

from __future__ import annotations

import os
from pathlib import Path

#: The variable holding a World Labs API key. Documented in capture/README.md, deliberately
#: not in the repo-root .env.example.
KEY_VAR = "WORLDLABS_API_KEY"

#: Where captures are written when nothing else is asked for. Shared with the rest of the
#: project only in the sense that both read the same environment variable.
DIR_VAR = "SPLAT_CAPTURE_DIR"

#: capture/marble/config.py -> capture/marble -> capture -> the repo root.
REPO_ROOT = Path(__file__).resolve().parents[2]


class MissingKey(RuntimeError):
    """No API key anywhere. The message says how to fix it, never what was found."""


def read_dotenv(path: Path) -> dict[str, str]:
    """Parse a ``.env`` into a dict. Absent file is not an error.

    Deliberately minimal: ``KEY=value`` lines, ``#`` comments, optional surrounding quotes.
    No interpolation and no ``export``. A dotenv parser that does more is a dependency, and
    this needs to read one variable.
    """
    if not path.is_file():
        return {}

    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[name.strip()] = value
    return values


def api_key(env: dict[str, str] | None = None, dotenv: Path | None = None) -> str:
    """The API key, from the environment or the repo-root ``.env``.

    Both arguments exist so tests never touch the real environment or the real file.
    """
    env = os.environ if env is None else env
    key = (env.get(KEY_VAR) or "").strip()
    if not key:
        path = REPO_ROOT / ".env" if dotenv is None else dotenv
        key = (read_dotenv(path).get(KEY_VAR) or "").strip()

    if not key:
        raise MissingKey(
            f"no {KEY_VAR}. Set it in the environment, or add a line to the repo-root .env "
            f"(which is gitignored):\n\n    {KEY_VAR}=your-key-here\n\n"
            f"A key comes from https://marble.worldlabs.ai/ -- the API needs credits, "
            f"minimum purchase $5.00."
        )
    return key


def describe_key(key: str) -> str:
    """A key as it is safe to print: a short prefix and a length. Never the value.

    Enough to tell "the wrong key" from "no key" and from "the key with a newline in it",
    which is what anyone reading this line is actually trying to work out.
    """
    if not key:
        return "no key"
    visible = key[:6] if len(key) > 12 else key[:2]
    return f"{visible}... ({len(key)} chars)"


def capture_dir(env: dict[str, str] | None = None, default: Path | None = None) -> Path:
    """Where captures land: ``SPLAT_CAPTURE_DIR`` if set, else ``capture/out``.

    Not created here. A directory made as a side effect of reading configuration is a
    directory that appears in odd places when a command fails early.
    """
    env = os.environ if env is None else env
    configured = (env.get(DIR_VAR) or "").strip()
    if configured:
        return Path(configured).expanduser()
    return (REPO_ROOT / "capture" / "out") if default is None else default


__all__ = [
    "DIR_VAR",
    "KEY_VAR",
    "REPO_ROOT",
    "MissingKey",
    "api_key",
    "capture_dir",
    "describe_key",
    "read_dotenv",
]
