"""Test plumbing: a fake API and a fake clock.

No test in this suite touches the network, reads the real environment, or sleeps. That is not
tidiness -- a client whose retry logic can only be exercised against a live server is a client
whose retry logic is never exercised.
"""

from __future__ import annotations

import json
import struct
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest

from marble.client import Marble

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"

Route = Callable[[httpx.Request], httpx.Response]


def splat_ply(count: int = 4, degree: int = 3, truncate: int = 0) -> bytes:
    """A minimal but genuine 3DGS PLY, in the layout a trainer writes.

    Shared, because the verifier is not the only thing that needs a plausible file: the
    pipeline tests download one, and a download test that returns a placeholder blob proves
    the download and nothing beyond it.
    """
    rest = {0: 0, 1: 9, 2: 24, 3: 45}[degree]
    names = (
        ["x", "y", "z", "nx", "ny", "nz", "f_dc_0", "f_dc_1", "f_dc_2"]
        + [f"f_rest_{i}" for i in range(rest)]
        + ["opacity", "scale_0", "scale_1", "scale_2", "rot_0", "rot_1", "rot_2", "rot_3"]
    )

    header = "ply\nformat binary_little_endian 1.0\n"
    header += f"element vertex {count}\n"
    header += "".join(f"property float {n}\n" for n in names)
    header += "end_header\n"

    body = b"".join(struct.pack("<f", 0.1 * i) for i in range(count * len(names)))
    data = header.encode("ascii") + body
    return data[: len(data) - truncate] if truncate else data


def fixture(name: str) -> dict[str, Any]:
    """One recorded response body. See fixtures/README.md on their provenance."""
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


def json_response(name: str, status: int = 200) -> httpx.Response:
    return httpx.Response(status, json=fixture(name))


class Recorder:
    """Every request the client made, so a test can assert on what went out.

    Half the failures worth catching here are about what was *sent* -- an API key attached to
    a signed upload URL, a retry of something that should never be retried -- and none of
    those are visible in the response.
    """

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []

    def __len__(self) -> int:
        return len(self.requests)

    def paths(self) -> list[str]:
        return [r.url.path for r in self.requests]

    def to(self, path: str) -> list[httpx.Request]:
        return [r for r in self.requests if r.url.path.endswith(path)]


class Clock:
    """A fake clock. Records what was waited for instead of waiting."""

    def __init__(self) -> None:
        self.waits: list[float] = []
        self.now = 0.0

    def sleep(self, seconds: float) -> None:
        self.waits.append(seconds)
        self.now += seconds

    def monotonic(self) -> float:
        return self.now

    @property
    def total(self) -> float:
        return sum(self.waits)


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def recorder() -> Recorder:
    return Recorder()


@pytest.fixture
def make_client(recorder: Recorder, clock: Clock):
    """Build a client whose transport is a function from request to response."""

    def build(handler: Route, **kwargs: Any) -> Marble:
        def wrapped(request: httpx.Request) -> httpx.Response:
            recorder.requests.append(request)
            return handler(request)

        options: dict[str, Any] = {
            "api_key": "test-key-do-not-log",
            "http": httpx.Client(transport=httpx.MockTransport(wrapped)),
            "sleep": clock.sleep,
            # No randomness in a test: jitter is proven separately by asserting it is used.
            "jitter": lambda: 0.0,
        }
        options.update(kwargs)
        return Marble(**options)

    return build


@pytest.fixture
def routed(make_client):
    """A client backed by a {(method, path-suffix): response} table.

    ``responses`` may be a single response or a list, consumed one per call, which is how the
    poll tests walk an operation from IN_PROGRESS to done.
    """

    def build(table: dict[tuple[str, str], Any], **kwargs: Any) -> Marble:
        remaining = {
            key: list(value) if isinstance(value, list) else [value]
            for key, value in table.items()
        }

        def handler(request: httpx.Request) -> httpx.Response:
            for (method, suffix), queue in remaining.items():
                if request.method == method and request.url.path.endswith(suffix):
                    item = queue[0] if len(queue) == 1 else queue.pop(0)
                    return item(request) if callable(item) else item
            return httpx.Response(404, json={"error": {"message": f"no route for {request.url}"}})

        return make_client(handler, **kwargs)

    return build
