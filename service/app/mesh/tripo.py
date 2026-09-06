"""
Generated meshes for scanned objects.

Why a mesh at all
-----------------

Splats lifted out of a scan look right from where the scanner stood and wrong from anywhere
else, because the far side of an object was never observed. That is fine while the object
sits still and obvious the moment it is thrown: it tumbles and shows a hollow. No amount of
segmentation fixes an unobserved surface -- the data is not there.

So the object's APPEARANCE comes from a generated mesh, and its PHYSICS stays on the voxel
boxes measured from the splats. That split is deliberate. The boxes are already tested, the
solver never sees a mesh, and there is no convex decomposition anywhere in the pipeline.

Caching, which is the part that makes it demoable
-------------------------------------------------

Generation takes 30-60 seconds and costs credits. Both are fine offline and neither is
acceptable in front of an audience, so every result is written to disk under the hash of the
image that produced it. The same selection photographed the same way returns instantly and
without a network call, which means a mesh generated once the night before is a mesh the
demo can rely on.

`MESH_DIR` doubles as a drop folder: a `.glb` placed there under a known name is served like
any other, so the pipeline can be shown end to end with no API at all.
"""

from __future__ import annotations

import hashlib
import os
import pathlib
import time
from dataclasses import dataclass

import httpx

API = "https://api.tripo3d.ai/v2/openapi"

#: Where generated and hand-placed meshes live. Outside the repo: these are large binaries.
_DEFAULT_MESH_DIR = pathlib.Path(__file__).resolve().parents[3] / "assets" / "meshes"
#: Blank counts as unset: a .env template carries its keys with empty values, and an empty
#: string here resolves to the working directory, which silently holds no meshes.
_configured = os.environ.get("RSRSPLAT_MESH_DIR", "").strip()
MESH_DIR = pathlib.Path(_configured) if _configured else _DEFAULT_MESH_DIR


class TripoError(RuntimeError):
    """Generation failed. The message is meant to be shown to the user as-is."""


@dataclass
class Mesh:
    """A generated mesh, on disk and ready to be served."""

    key: str
    path: pathlib.Path
    #: True when this came from the cache rather than the API.
    cached: bool

    @property
    def url(self) -> str:
        """Served relative to the mesh mount, including any subdirectory it sits in."""
        try:
            return "/meshes/" + self.path.relative_to(MESH_DIR).as_posix()
        except ValueError:
            return f"/meshes/{self.path.name}"


def key_for(image: bytes) -> str:
    """Cache key: the image decides the mesh, so the image's hash names it."""
    return hashlib.sha256(image).hexdigest()[:16]


#: Meshes chosen by hand, matched against the prompt. See `pinned`.
PINNED_DIR_NAME = "pinned"


def pinned(prompt: str) -> Mesh | None:
    """A mesh chosen by hand for this prompt, if there is one.

    The content cache is keyed on the image, which is right for avoiding duplicate work and
    useless for a demo: the second run photographs the object from a slightly different
    camera, the bytes differ, the hash differs, and a minute of generation happens again on
    stage. Pinning removes the guesswork -- put ``vest.glb`` in ``meshes/pinned/`` and any
    prompt containing "vest" uses it, every time, with no network call.

    Matching is by the file's own stem appearing in the prompt, longest first so that
    ``high_vis_vest.glb`` beats ``vest.glb`` when both could apply. ``default.glb`` matches
    anything, for the case where there is only one object in the demo and naming it is fuss.
    """
    directory = MESH_DIR / PINNED_DIR_NAME
    if not directory.is_dir():
        return None

    words = prompt.lower()
    candidates = sorted(directory.glob("*.glb"), key=lambda p: len(p.stem), reverse=True)
    for path in candidates:
        stem = path.stem.lower()
        if stem != "default" and stem.replace("_", " ") in words:
            return Mesh(path.stem, path, cached=True)

    fallback = directory / "default.glb"
    return Mesh("default", fallback, cached=True) if fallback.is_file() else None


def cached(image: bytes) -> Mesh | None:
    """The mesh for this image, if it has been generated before."""
    key = key_for(image)
    path = MESH_DIR / f"{key}.glb"
    return Mesh(key, path, cached=True) if path.exists() else None


def find(name: str) -> pathlib.Path | None:
    """A mesh by file name, for the drop-folder path. Refuses to leave MESH_DIR."""
    candidate = (MESH_DIR / name).resolve()
    if MESH_DIR.resolve() not in candidate.parents:
        return None
    return candidate if candidate.is_file() and candidate.suffix == ".glb" else None


def generate(image: bytes, *, api_key: str | None = None, timeout: float = 240.0) -> Mesh:
    """Image in, GLB on disk out. Returns immediately if this image is already cached.

    Blocking on purpose: the caller runs it off the event loop. Polling an external job from
    inside the socket handler would stall the pose stream for a minute, which looks exactly
    like the simulation having crashed.
    """
    hit = cached(image)
    if hit is not None:
        return hit

    key = api_key or os.environ.get("TRIPO_API_KEY", "")
    if not key:
        raise TripoError("TRIPO_API_KEY is not set; put it in .env")

    headers = {"Authorization": f"Bearer {key}"}
    MESH_DIR.mkdir(parents=True, exist_ok=True)

    with httpx.Client(timeout=60.0) as client:
        upload = client.post(
            f"{API}/upload", headers=headers, files={"file": ("view.png", image, "image/png")}
        )
        body = _ok(upload, "upload the image")
        token = body["data"]["image_token"]

        created = client.post(
            f"{API}/task",
            headers=headers,
            json={"type": "image_to_model", "file": {"type": "png", "file_token": token}},
        )
        task_id = _ok(created, "start generation")["data"]["task_id"]

        url = _await_model(client, headers, task_id, timeout)
        data = client.get(url, timeout=120.0).content

    path = MESH_DIR / f"{key_for(image)}.glb"
    path.write_bytes(data)
    return Mesh(key_for(image), path, cached=False)


def _await_model(client: httpx.Client, headers: dict, task_id: str, timeout: float) -> str:
    """Poll until the task produces a model, or give up with a message worth reading."""
    deadline = time.time() + timeout
    delay = 2.0
    while time.time() < deadline:
        body = _ok(client.get(f"{API}/task/{task_id}", headers=headers), "check generation")
        data = body["data"]
        status = data.get("status")
        if status == "success":
            output = data.get("output") or {}
            # Field name has moved between API versions; take whichever is present rather
            # than failing on a mesh that was generated successfully.
            for field in ("pbr_model", "model", "base_model"):
                if output.get(field):
                    return output[field]
            raise TripoError(f"generation finished with no model in {sorted(output)}")
        if status in {"failed", "cancelled", "banned", "expired"}:
            raise TripoError(f"generation {status}: {data.get('message') or 'no reason given'}")
        time.sleep(delay)
        delay = min(delay * 1.4, 8.0)
    raise TripoError(f"generation did not finish within {timeout:.0f}s")


def _ok(response: httpx.Response, what: str) -> dict:
    """Tripo reports failure in the BODY as well as the status, so both are checked.

    A 200 with ``code`` non-zero is a failure that would otherwise be read as success, and
    the credit error arrives as a 403 whose message is the only useful part of it.
    """
    try:
        body = response.json()
    except ValueError:
        raise TripoError(f"could not {what}: HTTP {response.status_code}") from None

    if response.status_code != 200 or body.get("code") not in (0, None):
        message = body.get("message") or response.text[:200]
        suggestion = body.get("suggestion")
        tail = f" ({suggestion})" if suggestion else ""
        raise TripoError(f"could not {what}: {message}{tail}")
    return body
