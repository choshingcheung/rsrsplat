"""The whole capture, end to end, with no network.

The happy path is the least interesting test here. What these are really for is the promise
the ledger makes: that a process killed at any point leaves a run which can be picked up
rather than paid for a second time.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from conftest import json_response, splat_ply

from marble import capture as pipeline
from marble.client import MODELS, MarbleError
from marble.run import Ledger
from marble.verify import Truncated

WORLD_ID = "9f1c2a44-0c3f-4a2e-9b77-2f0d6c5e1a01"
OP = "op_gen_7c1d4b2a"
PLY = splat_ply(count=500, degree=3)


def api(overrides: dict | None = None):
    """The routes a complete run touches, with any of them replaceable per test."""
    table = {
        ("GET", "/credits"): json_response("credits"),
        ("POST", "/media-assets:prepare_upload"): json_response("prepare_upload"),
        ("PUT", "/3b0f8e21-77aa-4a6d-9c11-8e2b5d4f0a90/kitchen.jpg"): httpx.Response(200),
        ("POST", "/worlds:generate"): json_response("generate"),
        ("GET", f"/operations/{OP}"): [
            json_response("operation_running"),
            json_response("operation_done"),
        ],
        ("POST", f"/worlds/{WORLD_ID}:export"): json_response("export_done"),
        ("GET", f"/wl-exports/{WORLD_ID}.ply"): httpx.Response(200, content=PLY),
        ("GET", f"/worlds/{WORLD_ID}"): json_response("world"),
    }
    table.update(overrides or {})
    return table


@pytest.fixture
def ledger(tmp_path: Path) -> Ledger:
    return Ledger(tmp_path / "runs")


@pytest.fixture
def photo(tmp_path: Path) -> Path:
    path = tmp_path / "kitchen.jpg"
    path.write_bytes(b"\xff\xd8\xff not really a jpeg")
    return path


def run_it(client, ledger: Ledger, out: Path, photo: Path | None = None, **kwargs):
    run = pipeline.start(
        client,
        ledger,
        text=kwargs.pop("text", "a domestic kitchen"),
        images=[photo] if photo else [],
        azimuths=[0.0] if photo else [],
        model=MODELS["draft"],
        model_alias="draft",
        display_name="kitchen",
        **kwargs,
    )
    return pipeline.advance(client, run, out_dir=out)


# ---------------------------------------------------------------------------------------
# The whole thing
# ---------------------------------------------------------------------------------------


def test_a_capture_ends_in_a_verified_file_and_a_sidecar(routed, ledger, tmp_path, photo):
    run = run_it(routed(api()), ledger, tmp_path / "out", photo)

    assert run.status == "verified"
    assert run.world_id == WORLD_ID
    assert run.splat_count == 500

    ply = Path(run.ply_path)
    assert ply.is_file() and ply.read_bytes() == PLY
    assert list(ply.parent.glob("*.part")) == []

    sidecar = json.loads(Path(run.sidecar_path).read_text(encoding="utf-8"))
    assert sidecar["ply"]["splat_count"] == 500
    assert sidecar["ply"]["sh_degree"] == 3
    assert sidecar["world"]["id"] == WORLD_ID


def test_the_sidecar_carries_what_marble_knows_about_scale(routed, ledger, tmp_path, photo):
    """The point of the sidecar: an independent answer to the ground plane question."""
    run = run_it(routed(api()), ledger, tmp_path / "out", photo)
    semantics = json.loads(Path(run.sidecar_path).read_text(encoding="utf-8"))["semantics"]

    assert semantics["metric_scale_factor"] == pytest.approx(1.8734)
    assert semantics["ground_plane_offset"] == pytest.approx(0.4162)
    assert "metres" in semantics["how_to_apply"]


def test_a_text_only_capture_needs_no_upload(routed, recorder, ledger, tmp_path):
    run = run_it(routed(api()), ledger, tmp_path / "out", photo=None)

    assert run.status == "verified"
    assert recorder.to("/media-assets:prepare_upload") == []


def test_an_image_is_uploaded_and_referenced_by_asset_id(routed, recorder, ledger, tmp_path, photo):
    run_it(routed(api()), ledger, tmp_path / "out", photo)

    sent = json.loads(recorder.to("/worlds:generate")[0].content)
    assert sent["world_prompt"]["image_prompt"]["media_asset_id"]
    assert sent["world_prompt"]["text_prompt"] == "a domestic kitchen"


# ---------------------------------------------------------------------------------------
# Money
# ---------------------------------------------------------------------------------------


def test_an_empty_account_is_refused_before_anything_is_spent(routed, recorder, ledger, tmp_path):
    poor = api({("GET", "/credits"): httpx.Response(200, json={"remaining_credits": 10})})
    client = routed(poor)

    with pytest.raises(pipeline.NotEnoughCredits, match="230 credits"):
        run_it(client, ledger, tmp_path / "out")

    assert recorder.to("/worlds:generate") == []
    assert ledger.all() == []


def test_the_estimate_recorded_is_the_estimate_quoted(routed, ledger, tmp_path, photo):
    run = run_it(routed(api()), ledger, tmp_path / "out", photo)
    assert run.estimated_credits == pipeline.price("a domestic kitchen", [photo], MODELS["draft"])
    assert run.estimated_credits == 230


# ---------------------------------------------------------------------------------------
# Crash and resume
# ---------------------------------------------------------------------------------------


def test_a_failed_generation_still_leaves_the_run_on_disk(routed, ledger, tmp_path):
    """The record is written first, so even a refusal is visible afterwards."""
    refused = api({("POST", "/worlds:generate"): json_response("error_400", status=400)})

    with pytest.raises(MarbleError):
        run_it(routed(refused), ledger, tmp_path / "out")

    recorded = ledger.all()
    assert len(recorded) == 1
    assert recorded[0].status == "failed"
    assert "could not be fetched" in recorded[0].error


def test_the_operation_id_lands_on_disk_immediately(routed, ledger, tmp_path):
    """A crash after this point is recoverable; before it, only `marble worlds` helps."""
    stalled = api({("GET", f"/operations/{OP}"): json_response("operation_running")})
    client = routed(stalled)

    run = pipeline.start(
        client,
        ledger,
        text="a kitchen",
        images=[],
        azimuths=[],
        model=MODELS["draft"],
        model_alias="draft",
        display_name="kitchen",
    )

    assert Ledger(ledger.directory).load(run.id).operation_id == OP


def test_a_run_killed_while_polling_is_resumed_not_repeated(routed, recorder, ledger, tmp_path):
    """The whole reason the ledger exists. Resume must not spend a second time."""
    crashed = ledger.new(
        display_name="kitchen",
        model="marble-1.0-draft",
        model_alias="draft",
        prompt_kind="text",
        prompt_text="a kitchen",
        estimated_credits=230,
    )
    crashed.update(status="generating", operation_id=OP)

    # A fresh process: reload from disk, and advance with no generate route at all.
    reopened = Ledger(ledger.directory).load(crashed.id)
    resumed = pipeline.advance(routed(api()), reopened, out_dir=tmp_path / "out")

    assert resumed.status == "verified"
    assert recorder.to("/worlds:generate") == []
    assert Path(resumed.ply_path).is_file()


def test_resuming_a_downloaded_run_does_not_export_again(routed, recorder, ledger, tmp_path):
    existing = tmp_path / "out" / "kitchen.ply"
    existing.parent.mkdir(parents=True)
    existing.write_bytes(PLY)

    run = ledger.new(display_name="kitchen", model="marble-1.0-draft", model_alias="draft")
    run.update(status="downloaded", world_id=WORLD_ID, ply_path=str(existing))

    finished = pipeline.advance(routed(api()), run, out_dir=tmp_path / "out")

    assert finished.status == "verified"
    assert recorder.to(":export") == []


def test_the_world_id_survives_a_failure_further_down(routed, ledger, tmp_path):
    """A download that dies must not take the world id with it -- the world is paid for."""
    broken = api({("POST", f"/worlds/{WORLD_ID}:export"): httpx.Response(500, text="nope")})

    with pytest.raises(MarbleError):
        run_it(routed(broken), ledger, tmp_path / "out")

    recorded = Ledger(ledger.directory).all()[0]
    assert recorded.world_id == WORLD_ID
    assert recorded.status == "failed"
    assert recorded.resumable


# ---------------------------------------------------------------------------------------
# Bad files
# ---------------------------------------------------------------------------------------


def test_a_truncated_download_fails_the_run_and_writes_no_ply(routed, ledger, tmp_path, photo):
    short = httpx.Response(200, headers={"Content-Length": "9999999"}, content=PLY[:200])
    broken = api({("GET", f"/wl-exports/{WORLD_ID}.ply"): short})

    with pytest.raises(MarbleError, match="truncated"):
        run_it(routed(broken), ledger, tmp_path / "out", photo)

    assert list((tmp_path / "out").glob("*.ply")) == []
    assert Ledger(ledger.directory).all()[0].status == "failed"


def test_a_file_that_is_not_splats_fails_verification(routed, ledger, tmp_path, photo):
    wrong = api(
        {
            ("GET", f"/wl-exports/{WORLD_ID}.ply"): httpx.Response(
                200, content=b'<?xml version="1.0"?><Error>ExpiredToken</Error>'
            )
        }
    )
    with pytest.raises(Exception, match="not a PLY"):
        run_it(routed(wrong), ledger, tmp_path / "out", photo)

    assert Ledger(ledger.directory).all()[0].status == "failed"


def test_a_truncated_body_the_server_did_not_measure_is_still_caught(
    routed, ledger, tmp_path, photo
):
    """No Content-Length, so only the PLY header's own arithmetic catches this."""
    silent = api(
        {("GET", f"/wl-exports/{WORLD_ID}.ply"): httpx.Response(200, content=PLY[:4000])}
    )
    with pytest.raises(Truncated):
        run_it(routed(silent), ledger, tmp_path / "out", photo)


def test_missing_world_metadata_does_not_lose_the_capture(routed, ledger, tmp_path, photo):
    """The file is downloaded and verified. A thinner sidecar is not worth failing over."""
    no_world = api({("GET", f"/worlds/{WORLD_ID}"): httpx.Response(500, text="nope")})
    run = run_it(routed(no_world), ledger, tmp_path / "out", photo)

    assert run.status == "verified"
    assert Path(run.ply_path).is_file()

    sidecar = json.loads(Path(run.sidecar_path).read_text(encoding="utf-8"))
    assert sidecar["ply"]["splat_count"] == 500
    assert "metric_scale_factor" not in sidecar["semantics"]


# ---------------------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------------------


def test_a_prompt_needs_something_in_it():
    with pytest.raises(ValueError):
        pipeline.build_prompt(None, [], [])


def test_a_run_is_priced_by_how_many_images_it_has(photo):
    """Multi-image buys a dearer panorama: 100 credits against 80."""
    assert pipeline.price("x", [photo], MODELS["draft"]) == 230
    assert pipeline.price("x", [], MODELS["draft"]) == 230
    assert pipeline.price("x", [photo, photo, photo], MODELS["draft"]) == 250
    assert pipeline.price("x", [photo], MODELS["standard"]) == 1580


def test_several_images_become_a_multi_image_prompt():
    prompt = pipeline.build_prompt("a desk", ["a", "b", "c"], [0.0, 20.0, 45.0])

    assert prompt["type"] == "multi-image"
    assert [v["azimuth"] for v in prompt["multi_image_prompt"]] == [0.0, 20.0, 45.0]
    assert prompt["text_prompt"] == "a desk"


def test_one_image_is_still_a_plain_image_prompt():
    assert pipeline.build_prompt(None, ["a"], [0.0])["type"] == "image"


def test_an_azimuth_missing_for_an_image_is_an_error_not_a_guess():
    """zip(strict=True): a silently dropped view is a worse outcome than a refusal."""
    with pytest.raises(ValueError):
        pipeline.build_prompt(None, ["a", "b", "c"], [0.0, 20.0])
