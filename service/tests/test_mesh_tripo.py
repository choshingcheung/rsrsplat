"""
Generated meshes: caching, path safety, and failures that must be readable.

No test here talks to Tripo. What matters locally is that a cached mesh never reaches the
network, that a failure arrives as a sentence someone can act on, and that a file name from
the browser cannot walk out of the mesh directory.
"""

from __future__ import annotations

import pathlib

import httpx
import pytest

from app.mesh import tripo


@pytest.fixture()
def mesh_dir(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> pathlib.Path:
    monkeypatch.setattr(tripo, "MESH_DIR", tmp_path)
    return tmp_path


def test_the_image_decides_the_key():
    """The image is the whole prompt, so it names the mesh."""
    assert tripo.key_for(b"one") == tripo.key_for(b"one")
    assert tripo.key_for(b"one") != tripo.key_for(b"two")


def test_a_cached_mesh_is_found_without_the_network(mesh_dir: pathlib.Path):
    image = b"pretend png" * 20
    assert tripo.cached(image) is None

    (mesh_dir / f"{tripo.key_for(image)}.glb").write_bytes(b"glb")
    hit = tripo.cached(image)
    assert hit is not None and hit.cached
    assert hit.url == f"/meshes/{tripo.key_for(image)}.glb"


def test_generate_returns_the_cache_and_never_calls_out(mesh_dir, monkeypatch):
    """The property the demo rests on: generate once, then it is free and instant."""
    image = b"pretend png" * 20
    (mesh_dir / f"{tripo.key_for(image)}.glb").write_bytes(b"glb")

    def explode(*_args, **_kwargs):
        raise AssertionError("a cached mesh must not touch the network")

    monkeypatch.setattr(httpx, "Client", explode)
    assert tripo.generate(image, api_key="unused").cached is True


def test_a_missing_key_says_so_rather_than_failing_at_the_socket(mesh_dir, monkeypatch):
    monkeypatch.delenv("TRIPO_API_KEY", raising=False)
    with pytest.raises(tripo.TripoError, match="TRIPO_API_KEY"):
        tripo.generate(b"x" * 200)


def test_a_drop_folder_mesh_is_found(mesh_dir: pathlib.Path):
    """No API, no credits: a GLB placed by hand is served like any other."""
    (mesh_dir / "vest.glb").write_bytes(b"glb")
    assert tripo.find("vest.glb") is not None
    assert tripo.find("missing.glb") is None


def test_a_name_cannot_walk_out_of_the_mesh_directory(mesh_dir: pathlib.Path):
    """The name comes from the browser, so it is not trusted."""
    secret = mesh_dir.parent / "secret.glb"
    secret.write_bytes(b"glb")
    assert tripo.find("../secret.glb") is None
    assert tripo.find("vest.txt") is None


class _Response:
    def __init__(self, status: int, payload: dict, text: str = ""):
        self.status_code = status
        self._payload = payload
        self.text = text

    def json(self) -> dict:
        return self._payload


def test_a_200_carrying_a_failure_code_is_a_failure():
    """Tripo reports failure in the body as well as the status; a 200 can still be an error."""
    with pytest.raises(tripo.TripoError, match="quota"):
        tripo._ok(_Response(200, {"code": 1004, "message": "quota exceeded"}), "start generation")


def test_the_credit_error_reaches_the_user_intact():
    """Only they can fix it, so the message and the suggestion both survive."""
    with pytest.raises(tripo.TripoError) as caught:
        tripo._ok(
            _Response(
                403,
                {
                    "code": 2010,
                    "message": "You don't have enough credit to create this task",
                    "suggestion": "Please purchase more credit",
                },
            ),
            "start generation",
        )
    assert "enough credit" in str(caught.value)
    assert "purchase" in str(caught.value)


def test_a_non_json_response_does_not_raise_a_json_error():
    with pytest.raises(tripo.TripoError, match="HTTP 502"):
        tripo._ok(_BadJson(502), "upload the image")


class _BadJson(_Response):
    def __init__(self, status: int):
        super().__init__(status, {}, text="<html>gateway</html>")

    def json(self) -> dict:
        raise ValueError("not json")


# ---------------------------------------------------------------------------------------
# Pinned meshes: the ones a demo actually runs on
# ---------------------------------------------------------------------------------------


def pin(directory: pathlib.Path, name: str) -> pathlib.Path:
    pinned = directory / tripo.PINNED_DIR_NAME
    pinned.mkdir(exist_ok=True)
    path = pinned / f"{name}.glb"
    path.write_bytes(b"glb")
    return path


def test_a_pinned_mesh_is_chosen_by_name(mesh_dir: pathlib.Path):
    """The property a demo needs: same prompt, same mesh, every run, no network."""
    pin(mesh_dir, "vest")
    hit = tripo.pinned("a heavy vest")
    assert hit is not None
    assert hit.path.name == "vest.glb"
    assert hit.url == "/meshes/pinned/vest.glb"


def test_the_most_specific_pin_wins(mesh_dir: pathlib.Path):
    """Otherwise adding a second, more precise mesh would be a coin toss."""
    pin(mesh_dir, "vest")
    pin(mesh_dir, "high_vis_vest")
    hit = tripo.pinned("a high vis vest on the floor")
    assert hit is not None and hit.path.stem == "high_vis_vest"


def test_underscores_in_a_file_name_read_as_spaces(mesh_dir: pathlib.Path):
    """A file cannot be called "high vis vest.glb" comfortably, but the prompt says that."""
    pin(mesh_dir, "tool_box")
    assert tripo.pinned("the tool box under the desk") is not None


def test_default_matches_anything(mesh_dir: pathlib.Path):
    """For the common demo case: one object, and naming it is fuss."""
    pin(mesh_dir, "default")
    assert tripo.pinned("literally anything") is not None


def test_a_named_pin_beats_the_default(mesh_dir: pathlib.Path):
    pin(mesh_dir, "default")
    pin(mesh_dir, "vest")
    hit = tripo.pinned("a vest")
    assert hit is not None and hit.path.stem == "vest"


def test_no_pins_means_no_pin(mesh_dir: pathlib.Path):
    assert tripo.pinned("a vest") is None
    pin(mesh_dir, "chair")
    assert tripo.pinned("a vest") is None

