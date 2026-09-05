"""The World Labs Marble (World) API, as much of it as this tool needs.

Six calls, in the order a capture happens:

    1. POST /media-assets:prepare_upload   ask where to put an image
    2. PUT  <signed url>                   put it there
    3. POST /worlds:generate               start a world, get an operation
    4. GET  /operations/{id}               poll, about five minutes
    5. POST /worlds/{id}:export            ask for a PLY, get another operation
    6. GET  <signed url>                   download it

Everything is injectable -- the HTTP transport, the clock, the jitter -- because the whole
client has to be provable with no key, no credits and no network. The tests drive all six
against recorded shapes through ``httpx.MockTransport``.

**Three things about this API that are not guessable:**

- The upload in step 2 goes to a *signed* URL and must NOT carry the API key. It carries the
  headers the prepare call handed back, and nothing else. Sending our own auth to Google's
  signed endpoint is a 403 that reads exactly like an expired key and is not one.
- **Rate limits apply to generation starts, not to polling.** Three a minute on the default
  tier. So step 4 can poll at a sane cadence, and it is step 3 that needs the backoff.
- A PLY export is *free* and usually returns already done, because it is a cached conversion
  of a world that has already been paid for. The money is all in step 3.
"""

from __future__ import annotations

import random
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

BASE_URL = "https://api.worldlabs.ai/marble/v1"

#: The header the API authenticates with. Not ``Authorization``, and not a bearer token.
KEY_HEADER = "WLT-Api-Key"

#: $1.00 buys this many credits, at the published fixed rate. Only ever used to turn an
#: estimate into a number a human can consent to.
CREDITS_PER_USD = 1250


@dataclass(frozen=True)
class Model:
    """A model, and what starting it costs.

    ``extra`` is the worst case on top of ``base``: the plus tier bills a variable amount for
    the second stage, so an estimate that ignored it would under-quote by half. Estimates
    round UP, because the failure that matters is spending more than someone agreed to.
    """

    name: str
    base: int
    extra: int = 0

    @property
    def worst_case(self) -> int:
        return self.base + self.extra


#: Aliases, so nobody has to type a version string to get the cheap one.
MODELS: dict[str, Model] = {
    "draft": Model("marble-1.0-draft", 150),
    "standard": Model("marble-1.1", 1500),
    "plus": Model("marble-1.1-plus", 1500, extra=1500),
}

#: What generating the panorama costs, by the kind of prompt it is made from. A panorama
#: given as input costs nothing because there is nothing to generate.
PANO_CREDITS = {"text": 80, "image": 80, "pano": 0, "multi-image": 100, "video": 100}

DEFAULT_MODEL = "draft"


# ---------------------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------------------


class MarbleError(RuntimeError):
    """Anything the API said no to. Readable enough to print at a terminal."""


class AuthError(MarbleError):
    """The key was rejected. Never carries the key itself -- see ``config.describe_key``."""


class InsufficientCredits(MarbleError):
    """402. Distinct from a generic failure because the fix is entirely different."""


class BadRequest(MarbleError):
    """400 or 422: a malformed request, an unreachable image, or a content policy refusal."""


class NotFound(MarbleError):
    """404. A world or operation id that does not exist, usually a typo from the ledger."""


class RateLimited(MarbleError):
    """429. Carries ``retry_after`` when the server sent one."""

    def __init__(self, message: str, retry_after: float | None = None):
        super().__init__(message)
        self.retry_after = retry_after


class ServerError(MarbleError):
    """5xx. Retried automatically; only reaches a caller once the retries are spent."""


class Timeout(MarbleError):
    """An operation did not finish inside the budget. The world may still be generating."""


def _message(response: httpx.Response) -> str:
    """The server's own words if it sent any, else the status line.

    A JSON error body is far more use than "request failed", and this API puts a real
    sentence in ``error.message`` -- including for content policy refusals, which are the one
    failure a user can actually act on.
    """
    try:
        body = response.json()
    except Exception:
        text = (response.text or "").strip()
        return text[:400] or f"HTTP {response.status_code}"

    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict):
            detail = error.get("message") or error.get("code")
            if detail:
                return str(detail)
        for key in ("message", "detail", "error"):
            value = body.get(key)
            if isinstance(value, str) and value:
                return value
    return f"HTTP {response.status_code}"


def _raise_for(response: httpx.Response) -> None:
    status = response.status_code
    if status < 400:
        return
    detail = _message(response)

    if status in (401, 403):
        raise AuthError(f"the API rejected this key ({detail}). Check WORLDLABS_API_KEY.")
    if status == 402:
        raise InsufficientCredits(
            f"out of API credits ({detail}). Buy more at https://marble.worldlabs.ai/ "
            f"-- minimum $5.00, which is about 27 draft worlds."
        )
    if status == 404:
        raise NotFound(detail)
    if status == 429:
        header = response.headers.get("Retry-After")
        wait: float | None = None
        if header:
            try:
                wait = float(header)
            except ValueError:
                wait = None
        raise RateLimited(f"rate limited ({detail})", retry_after=wait)
    if status in (400, 422):
        raise BadRequest(detail)
    if status >= 500:
        raise ServerError(f"the API failed ({status}): {detail}")
    raise MarbleError(f"HTTP {status}: {detail}")


# ---------------------------------------------------------------------------------------
# Shapes
# ---------------------------------------------------------------------------------------


@dataclass(frozen=True)
class UploadTarget:
    """Where to PUT one file, and with which headers. Both come from the server."""

    media_asset_id: str
    upload_url: str
    method: str
    headers: dict[str, str]


@dataclass(frozen=True)
class Operation:
    """A long-running job. Generation and export both come back as one of these."""

    operation_id: str
    done: bool
    error: dict[str, Any] | None = None
    metadata: dict[str, Any] | None = None
    response: dict[str, Any] | None = None

    @classmethod
    def parse(cls, body: dict[str, Any]) -> Operation:
        return cls(
            operation_id=str(body.get("operation_id") or body.get("id") or ""),
            done=bool(body.get("done")),
            error=body.get("error"),
            metadata=body.get("metadata"),
            response=body.get("response"),
        )

    @property
    def status(self) -> str:
        """``IN_PROGRESS``, ``SUCCEEDED`` and so on, when the server is reporting progress."""
        progress = (self.metadata or {}).get("progress") or {}
        status = progress.get("status")
        if status:
            return str(status)
        return "SUCCEEDED" if self.done else "IN_PROGRESS"

    @property
    def world_id(self) -> str | None:
        """The world this operation is building.

        Present in ``metadata`` while it runs and in ``response`` once it is finished, and
        this reads both -- the id is worth having as early as possible, because it is what
        makes a paid generation recoverable after a crash.
        """
        meta = (self.metadata or {}).get("world_id")
        if meta:
            return str(meta)
        response = self.response or {}
        return str(response["id"]) if response.get("id") else None

    @property
    def download_url(self) -> str | None:
        return (self.response or {}).get("url")

    def raise_if_failed(self) -> Operation:
        if self.error:
            code = self.error.get("code", "")
            message = self.error.get("message") or "the operation failed"
            raise MarbleError(f"{message}{f' [{code}]' if code else ''}")
        return self


def text_prompt(text: str) -> dict[str, Any]:
    return {"type": "text", "text_prompt": text}


def image_prompt(
    *,
    media_asset_id: str | None = None,
    uri: str | None = None,
    text: str | None = None,
    is_pano: str = "auto",
) -> dict[str, Any]:
    """An image prompt, from an uploaded asset or a public URL.

    ``is_pano="auto"`` lets the server detect an equirectangular panorama, which is what makes
    a 360 photo cost nothing to convert -- there is no panorama to generate.
    """
    if (media_asset_id is None) == (uri is None):
        raise ValueError("give exactly one of media_asset_id or uri")

    content: dict[str, Any]
    if media_asset_id is not None:
        content = {"source": "media_asset", "media_asset_id": media_asset_id}
    else:
        content = {"source": "uri", "uri": uri}

    prompt: dict[str, Any] = {"type": "image", "image_prompt": content, "is_pano": is_pano}
    if text:
        prompt["text_prompt"] = text
    return prompt


def prompt_kind(world_prompt: dict[str, Any]) -> str:
    """The prompt's type, as the pano price list names it."""
    kind = str(world_prompt.get("type", "text"))
    if kind == "image" and world_prompt.get("is_pano") == "true":
        return "pano"
    return kind


def estimate_credits(world_prompt: dict[str, Any], model: Model) -> int:
    """Worst-case credits for one generation. Rounds up, never down."""
    return model.worst_case + PANO_CREDITS.get(prompt_kind(world_prompt), 100)


def usd(credits: int) -> float:
    return credits / CREDITS_PER_USD


# ---------------------------------------------------------------------------------------
# The client
# ---------------------------------------------------------------------------------------


@dataclass
class Marble:
    """A thin, synchronous client. One instance per command.

    ``http``, ``sleep`` and ``jitter`` are injected so the tests can drive every retry path
    without a network and without waiting: backoff proven against a fake clock is proven,
    backoff proven by sleeping is just slow.
    """

    api_key: str
    base_url: str = BASE_URL
    http: httpx.Client = field(default_factory=lambda: httpx.Client(timeout=60.0))
    sleep: Callable[[float], None] = time.sleep
    jitter: Callable[[], float] = random.random
    max_retries: int = 4
    backoff: float = 2.0

    # -- plumbing ------------------------------------------------------------------------

    def _url(self, path: str) -> str:
        return f"{self.base_url.rstrip('/')}/{path.lstrip('/')}"

    def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        """One authenticated call, retried on 429 and 5xx, and never on anything else.

        Retrying a 400 just spends the same wrong request four times, and retrying a 402
        cannot conjure credits. Only the two failures that are genuinely transient come back.
        """
        headers = {KEY_HEADER: self.api_key, "Accept": "application/json"}
        headers.update(kwargs.pop("headers", None) or {})

        last: MarbleError | None = None
        for attempt in range(self.max_retries + 1):
            try:
                response = self.http.request(method, self._url(path), headers=headers, **kwargs)
                _raise_for(response)
                if not response.content:
                    return {}
                return response.json()
            except (RateLimited, ServerError) as exc:
                last = exc
                if attempt >= self.max_retries:
                    break
                # Honour Retry-After when the server sent one; otherwise exponential with
                # jitter, so a fleet of clients does not synchronise its retries.
                wait = getattr(exc, "retry_after", None)
                if wait is None:
                    wait = (self.backoff**attempt) + self.jitter()
                self.sleep(float(wait))
            except httpx.TimeoutException as exc:
                last = ServerError(f"the API did not respond in time: {exc}")
                if attempt >= self.max_retries:
                    break
                self.sleep((self.backoff**attempt) + self.jitter())
            except httpx.HTTPError as exc:
                raise MarbleError(f"could not reach the API: {exc}") from exc

        assert last is not None
        raise last

    # -- credits -------------------------------------------------------------------------

    def credits(self) -> int:
        """Remaining API credits. Checked before spending, never after."""
        body = self._request("GET", "/credits")
        return int(body.get("remaining_credits", 0))

    # -- media ---------------------------------------------------------------------------

    def prepare_upload(self, file_name: str, extension: str, kind: str = "image") -> UploadTarget:
        """Ask for somewhere to put a file. ``extension`` carries no dot."""
        body = self._request(
            "POST",
            "/media-assets:prepare_upload",
            json={
                "file_name": file_name,
                "extension": extension.lstrip("."),
                "kind": kind,
            },
        )
        asset = body.get("media_asset") or {}
        info = body.get("upload_info") or {}
        if not asset.get("id") or not info.get("upload_url"):
            raise MarbleError(f"prepare_upload returned no upload target: {body}")

        return UploadTarget(
            media_asset_id=str(asset["id"]),
            upload_url=str(info["upload_url"]),
            method=str(info.get("upload_method") or "PUT").upper(),
            headers=dict(info.get("required_headers") or {}),
        )

    def upload(self, target: UploadTarget, data: bytes, content_type: str | None = None) -> None:
        """Send the bytes to the signed URL.

        **No API key on this request.** It goes to storage, not to the API, and the signature
        in the URL is the whole of its authorisation. Adding our own auth header is a 403
        that looks like a key problem.
        """
        headers = dict(target.headers)
        if content_type:
            headers.setdefault("Content-Type", content_type)

        response = self.http.request(
            target.method, target.upload_url, content=data, headers=headers
        )
        if response.status_code >= 400:
            raise MarbleError(
                f"upload failed ({response.status_code}): {_message(response)}. "
                f"The signed URL carries its own authorisation and its required headers "
                f"must be sent exactly as given."
            )

    def upload_file(self, path: Path, kind: str = "image") -> str:
        """Prepare, upload, and hand back the media asset id."""
        data = path.read_bytes()
        target = self.prepare_upload(path.name, path.suffix.lstrip("."), kind=kind)
        self.upload(target, data, content_type=_content_type(path.suffix))
        return target.media_asset_id

    # -- worlds --------------------------------------------------------------------------

    def generate(
        self, world_prompt: dict[str, Any], model: Model, display_name: str | None = None
    ) -> Operation:
        """Start a world. **This is the call that spends credits.**"""
        payload: dict[str, Any] = {"model": model.name, "world_prompt": world_prompt}
        if display_name:
            payload["display_name"] = display_name
        return Operation.parse(self._request("POST", "/worlds:generate", json=payload))

    def operation(self, operation_id: str) -> Operation:
        return Operation.parse(self._request("GET", f"/operations/{operation_id}"))

    def world(self, world_id: str) -> dict[str, Any]:
        """A world, including its assets and (sometimes) its semantics metadata.

        The published reference describes the world nested under a ``world`` field. The live
        API returns it bare. Both are accepted -- reading through the wrong shape yields
        nothing at all rather than failing, and a sidecar missing its most valuable field is
        not something anyone notices.
        """
        body = self._request("GET", f"/worlds/{world_id}")
        inner = body.get("world")
        return inner if isinstance(inner, dict) else body

    def worlds(self, limit: int = 20) -> list[dict[str, Any]]:
        """Recent worlds. The recovery path when a local run record was lost.

        **POST, not GET, and ``worlds:list`` rather than ``worlds``.** ``GET /worlds`` is a
        404 and ``GET /worlds:list`` a 405; this was found by probing the live API, because
        the published reference does not spell the listing out. See capture/NOTES.md.
        """
        body = self._request("POST", "/worlds:list", json={"page_size": limit})
        items = body.get("worlds")
        return list(items) if isinstance(items, list) else []

    def poll(
        self,
        operation_id: str,
        *,
        interval: float = 5.0,
        timeout: float = 1800.0,
        on_progress: Callable[[Operation, float], None] | None = None,
        now: Callable[[], float] = time.monotonic,
    ) -> Operation:
        """Wait for an operation, reporting as it goes.

        Polling is not rate limited -- the published limits are on generation starts -- so the
        interval here is politeness rather than necessity. The timeout is a real budget: a
        generation takes about five minutes, and thirty is long enough that hitting it means
        something is wrong rather than slow.
        """
        started = now()
        while True:
            operation = self.operation(operation_id)
            if on_progress:
                on_progress(operation, now() - started)
            if operation.done:
                return operation.raise_if_failed()

            waited = now() - started
            if waited >= timeout:
                raise Timeout(
                    f"operation {operation_id} still {operation.status} after "
                    f"{waited:.0f}s. It may yet finish: resume this run rather than "
                    f"generating again, which would be charged a second time."
                )
            self.sleep(interval)

    # -- export --------------------------------------------------------------------------

    def export(
        self, world_id: str, asset_type: str = "splats", fmt: str = "ply", **extra: Any
    ) -> Operation:
        payload: dict[str, Any] = {"asset_type": asset_type, "format": fmt, **extra}
        return Operation.parse(
            self._request("POST", f"/worlds/{world_id}:export", json=payload)
        )

    def export_url(
        self,
        world_id: str,
        *,
        fmt: str = "ply",
        interval: float = 5.0,
        timeout: float = 900.0,
        on_progress: Callable[[Operation, float], None] | None = None,
    ) -> str:
        """Export and return a signed download URL.

        A PLY conversion is cached and usually comes back already done, so the poll is the
        exception rather than the rule -- but an export that has never been run once does
        take a moment, and returning a null URL would fail much later and less clearly.
        """
        operation = self.export(world_id, fmt=fmt).raise_if_failed()
        if not operation.done:
            operation = self.poll(
                operation.operation_id,
                interval=interval,
                timeout=timeout,
                on_progress=on_progress,
            )

        url = operation.download_url
        if not url:
            raise MarbleError(
                f"the export finished with no download URL: {operation.response!r}"
            )
        return url

    # -- download ------------------------------------------------------------------------

    def download(
        self,
        url: str,
        destination: Path,
        *,
        on_progress: Callable[[int, int | None], None] | None = None,
        chunk: int = 1 << 20,
    ) -> int:
        """Stream a signed URL to disk, and refuse to call a short file a success.

        Written to a ``.part`` and renamed only once the length checks out. A truncated
        download is still a file and still opens; the failure would otherwise surface as a
        confusing parse error hours later, in a different program.
        """
        destination.parent.mkdir(parents=True, exist_ok=True)
        partial = destination.with_suffix(destination.suffix + ".part")

        written = 0
        expected: int | None = None
        with self.http.stream("GET", url) as response:
            if response.status_code >= 400:
                response.read()
                raise MarbleError(f"download failed ({response.status_code}): {_message(response)}")

            header = response.headers.get("Content-Length")
            expected = int(header) if header and header.isdigit() else None

            with partial.open("wb") as handle:
                for block in response.iter_bytes(chunk):
                    handle.write(block)
                    written += len(block)
                    if on_progress:
                        on_progress(written, expected)

        if expected is not None and written != expected:
            partial.unlink(missing_ok=True)
            raise MarbleError(
                f"download truncated: got {written} bytes, expected {expected}. "
                f"Nothing was written to {destination.name}."
            )

        partial.replace(destination)
        return written


def world_id_of(world: dict[str, Any]) -> str | None:
    """A world's id, whichever name it came back under.

    The listing and the fetch both use ``world_id``; the reference documents ``id``. Reading
    only ``id`` prints a blank column and, worse, would write a null id into a sidecar.
    """
    value = world.get("world_id") or world.get("id")
    return str(value) if value else None


def semantics_of(world: dict[str, Any]) -> dict[str, Any]:
    """Marble's metric scale and ground plane for a world, if it has them.

    **Nested under ``assets.splats``, not at the top level**, and frequently ``null`` -- a
    draft world came back with nothing here at all. So this returns ``{}`` rather than
    raising, and the sidecar records its absence honestly instead of implying the numbers
    were never asked for. See capture/NOTES.md.
    """
    splats = ((world.get("assets") or {}).get("splats")) or {}
    metadata = splats.get("semantics_metadata")
    if isinstance(metadata, dict):
        return metadata
    # The documented top-level spelling, in case it moves back.
    top = world.get("semantics_metadata")
    return top if isinstance(top, dict) else {}


def _content_type(suffix: str) -> str:
    return {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
        ".mp4": "video/mp4",
        ".mov": "video/quicktime",
        ".mkv": "video/x-matroska",
    }.get(suffix.lower(), "application/octet-stream")


__all__ = [
    "BASE_URL",
    "CREDITS_PER_USD",
    "DEFAULT_MODEL",
    "KEY_HEADER",
    "MODELS",
    "PANO_CREDITS",
    "AuthError",
    "BadRequest",
    "InsufficientCredits",
    "Marble",
    "MarbleError",
    "Model",
    "NotFound",
    "Operation",
    "RateLimited",
    "ServerError",
    "Timeout",
    "UploadTarget",
    "estimate_credits",
    "image_prompt",
    "prompt_kind",
    "semantics_of",
    "text_prompt",
    "world_id_of",
    "usd",
]
