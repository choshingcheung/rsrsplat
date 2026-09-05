"""The client, every path, with no network and no clock.

The tests that matter most here are not the happy ones. They are: that a signed upload does
not carry our API key, that a 400 is never retried, and that a truncated download is not
reported as a success.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from conftest import fixture, json_response

from marble.client import (
    MODELS,
    AuthError,
    BadRequest,
    InsufficientCredits,
    MarbleError,
    NotFound,
    Operation,
    RateLimited,
    ServerError,
    Timeout,
    estimate_credits,
    image_prompt,
    prompt_kind,
    semantics_of,
    text_prompt,
    usd,
    world_id_of,
)

WORLD_ID = "9f1c2a44-0c3f-4a2e-9b77-2f0d6c5e1a01"


# ---------------------------------------------------------------------------------------
# Authentication and plumbing
# ---------------------------------------------------------------------------------------


def test_api_calls_carry_the_key_header(routed, recorder):
    client = routed({("GET", "/credits"): json_response("credits")})
    assert client.credits() == 6250
    assert recorder.requests[0].headers["WLT-Api-Key"] == "test-key-do-not-log"


def test_an_empty_body_is_not_a_parse_error(routed):
    client = routed({("GET", "/credits"): httpx.Response(200)})
    assert client.credits() == 0


# ---------------------------------------------------------------------------------------
# Upload
# ---------------------------------------------------------------------------------------


def test_prepare_upload_reads_the_target_out(routed):
    client = routed({("POST", "/media-assets:prepare_upload"): json_response("prepare_upload")})
    target = client.prepare_upload("kitchen.jpg", "jpg")

    assert target.media_asset_id == "3b0f8e21-77aa-4a6d-9c11-8e2b5d4f0a90"
    assert target.method == "PUT"
    assert target.headers == {"x-goog-content-length-range": "0,1048576000"}


def test_prepare_upload_sends_the_extension_without_a_dot(routed, recorder):
    client = routed({("POST", "/media-assets:prepare_upload"): json_response("prepare_upload")})
    client.prepare_upload("kitchen.jpg", ".jpg")

    import json as _json

    assert _json.loads(recorder.requests[0].content)["extension"] == "jpg"


def test_a_signed_upload_never_carries_our_api_key(routed, recorder, tmp_path: Path):
    """The signature in the URL is the whole authorisation.

    Sending our own key to the storage endpoint is a 403 that reads exactly like an expired
    key, and the hour it costs is spent looking at the wrong credential.
    """
    image = tmp_path / "kitchen.jpg"
    image.write_bytes(b"\xff\xd8\xff jpeg-ish bytes")

    client = routed(
        {
            ("POST", "/media-assets:prepare_upload"): json_response("prepare_upload"),
            ("PUT", "/wl-uploads/3b0f8e21"): httpx.Response(200),
        }
    )
    client.upload_file(image)

    put = recorder.to("/wl-uploads/3b0f8e21")[0]
    assert "WLT-Api-Key" not in put.headers
    assert put.headers["x-goog-content-length-range"] == "0,1048576000"
    assert put.headers["content-type"] == "image/jpeg"
    assert put.content == b"\xff\xd8\xff jpeg-ish bytes"


def test_a_failed_upload_says_what_the_signed_url_expects(routed, tmp_path: Path):
    image = tmp_path / "kitchen.jpg"
    image.write_bytes(b"x")

    client = routed(
        {
            ("POST", "/media-assets:prepare_upload"): json_response("prepare_upload"),
            ("PUT", "/wl-uploads/3b0f8e21"): httpx.Response(403, text="SignatureDoesNotMatch"),
        }
    )
    with pytest.raises(MarbleError, match="required headers"):
        client.upload_file(image)


# ---------------------------------------------------------------------------------------
# Generation
# ---------------------------------------------------------------------------------------


def test_generate_sends_the_model_and_the_prompt(routed, recorder):
    client = routed({("POST", "/worlds:generate"): json_response("generate")})
    operation = client.generate(
        image_prompt(media_asset_id="asset-1", text="a domestic kitchen"),
        MODELS["draft"],
        display_name="kitchen",
    )

    import json as _json

    sent = _json.loads(recorder.requests[0].content)
    assert sent["model"] == "marble-1.0-draft"
    assert sent["display_name"] == "kitchen"
    assert sent["world_prompt"]["type"] == "image"
    assert sent["world_prompt"]["image_prompt"] == {
        "source": "media_asset",
        "media_asset_id": "asset-1",
    }
    assert operation.operation_id == "op_gen_7c1d4b2a"
    assert operation.done is False


def test_listing_worlds_is_a_post_to_worlds_list(routed, recorder):
    """Found by probing the live API: GET /worlds is a 404, GET /worlds:list a 405.

    Pinned here because it is not in the published reference, so the only thing standing
    between this and a silent regression is a test.
    """
    client = routed({("POST", "/worlds:list"): json_response("worlds_list")})
    worlds = client.worlds(limit=5)

    assert recorder.requests[0].method == "POST"
    assert recorder.requests[0].url.path.endswith("/worlds:list")
    assert len(worlds) == 1
    assert world_id_of(worlds[0]) == WORLD_ID


def test_an_empty_account_lists_nothing_rather_than_failing(routed):
    empty = httpx.Response(200, json={"worlds": [], "next_page_token": None})
    client = routed({("POST", "/worlds:list"): empty})
    assert client.worlds() == []


def test_a_world_comes_back_bare_and_keyed_by_world_id(routed):
    """What the live API actually returns: no envelope, and ``world_id`` not ``id``.

    The published reference says both of these the other way round. Reading ``id`` off this
    put a blank column in `marble worlds` and would have written a null id into a sidecar.
    """
    client = routed({("GET", f"/worlds/{WORLD_ID}"): json_response("world")})
    world = client.world(WORLD_ID)

    assert world_id_of(world) == WORLD_ID
    assert "id" not in world


def test_a_wrapped_world_is_still_accepted(routed):
    """The documented shape. Tolerated so the tool survives the API moving back to it."""
    wrapped = httpx.Response(200, json={"world": {"world_id": WORLD_ID, "display_name": "k"}})
    client = routed({("GET", f"/worlds/{WORLD_ID}"): wrapped})
    assert world_id_of(client.world(WORLD_ID)) == WORLD_ID


def test_semantics_live_under_assets_splats_not_at_the_top(routed):
    """Nested two levels down. Read from the top level it is silently absent."""
    client = routed({("GET", f"/worlds/{WORLD_ID}"): json_response("world")})
    semantics = semantics_of(client.world(WORLD_ID))

    assert semantics["metric_scale_factor"] == pytest.approx(1.8734)
    assert semantics["ground_plane_offset"] == pytest.approx(0.4162)


def test_a_draft_world_reports_no_semantics_at_all(routed):
    """Observed: a real draft world came back with ``semantics_metadata: null``.

    So the metric scale is not something this tool can promise. It returns {} and the
    sidecar says so, rather than implying the numbers were never requested.
    """
    client = routed({("GET", f"/worlds/{WORLD_ID}"): json_response("world_without_semantics")})
    assert semantics_of(client.world(WORLD_ID)) == {}


def test_semantics_at_the_top_level_would_still_be_found():
    assert semantics_of({"semantics_metadata": {"metric_scale_factor": 2.0}}) == {
        "metric_scale_factor": 2.0
    }


def test_an_image_prompt_takes_an_asset_or_a_uri_but_not_both():
    with pytest.raises(ValueError):
        image_prompt(media_asset_id="a", uri="https://example.com/x.jpg")
    with pytest.raises(ValueError):
        image_prompt()


# ---------------------------------------------------------------------------------------
# Polling
# ---------------------------------------------------------------------------------------


def test_poll_waits_until_done_and_reports_progress(routed, clock):
    client = routed(
        {
            ("GET", "/operations/op_gen_7c1d4b2a"): [
                json_response("operation_running"),
                json_response("operation_running"),
                json_response("operation_done"),
            ]
        }
    )

    seen: list[str] = []
    operation = client.poll(
        "op_gen_7c1d4b2a",
        interval=5.0,
        on_progress=lambda op, _elapsed: seen.append(op.status),
        now=clock.monotonic,
    )

    assert operation.done
    assert seen == ["IN_PROGRESS", "IN_PROGRESS", "SUCCEEDED"]
    # Waited between polls, and not after the last one.
    assert clock.waits == [5.0, 5.0]


def test_the_world_id_is_available_before_the_world_is(routed):
    """The id is what makes a paid generation recoverable, so it is read from metadata."""
    running = Operation.parse(fixture("operation_running"))
    assert running.done is False
    assert running.world_id == WORLD_ID


def test_a_refused_generation_reports_what_the_server_said(routed, clock):
    client = routed({("GET", "/operations/op_gen_7c1d4b2a"): json_response("operation_failed")})
    with pytest.raises(MarbleError, match="content filter"):
        client.poll("op_gen_7c1d4b2a", now=clock.monotonic)


def test_a_timed_out_poll_says_not_to_generate_again(routed, clock):
    """The expensive mistake at this point is paying for a second world."""
    client = routed({("GET", "/operations/op_gen_7c1d4b2a"): json_response("operation_running")})
    with pytest.raises(Timeout, match="resume this run"):
        client.poll("op_gen_7c1d4b2a", interval=5.0, timeout=20.0, now=clock.monotonic)


# ---------------------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------------------


def test_a_cached_export_returns_a_url_without_polling(routed, clock):
    client = routed({("POST", f"/worlds/{WORLD_ID}:export"): json_response("export_done")})
    url = client.export_url(WORLD_ID)

    assert url.endswith(".ply?sig=xyz")
    assert clock.waits == []


def test_an_uncached_export_polls_until_the_url_arrives(routed, clock):
    client = routed(
        {
            ("POST", f"/worlds/{WORLD_ID}:export"): json_response("export_pending"),
            ("GET", "/operations/op_exp_3a9e5f10"): [
                json_response("export_pending"),
                json_response("export_done"),
            ],
        }
    )
    assert client.export_url(WORLD_ID, interval=3.0).endswith(".ply?sig=xyz")
    assert clock.waits == [3.0]


def test_export_asks_for_a_ply(routed, recorder):
    client = routed({("POST", f"/worlds/{WORLD_ID}:export"): json_response("export_done")})
    client.export_url(WORLD_ID)

    import json as _json

    assert _json.loads(recorder.requests[0].content) == {
        "asset_type": "splats",
        "format": "ply",
    }


def test_an_export_with_no_url_is_an_error_here_not_later(routed):
    done_but_empty = httpx.Response(
        200, json={"operation_id": "op_exp_3a9e5f10", "done": True, "response": {}}
    )
    client = routed({("POST", f"/worlds/{WORLD_ID}:export"): done_but_empty})
    with pytest.raises(MarbleError, match="no download URL"):
        client.export_url(WORLD_ID)


# ---------------------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------------------


def test_download_writes_the_file_and_leaves_no_part_behind(routed, tmp_path: Path):
    body = b"ply\nformat binary_little_endian 1.0\n" + b"\x00" * 512
    client = routed({("GET", "/wl-exports/world.ply"): httpx.Response(200, content=body)})

    destination = tmp_path / "kitchen.ply"
    written = client.download("https://storage.googleapis.com/wl-exports/world.ply", destination)

    assert written == len(body)
    assert destination.read_bytes() == body
    assert list(tmp_path.glob("*.part")) == []


def test_a_truncated_download_is_a_failure_not_a_file(routed, tmp_path: Path):
    """A short file still opens. Left alone, this surfaces as a parse error somewhere else."""
    short = httpx.Response(
        200,
        headers={"Content-Length": "999999"},
        content=b"only a few bytes",
    )
    client = routed({("GET", "/wl-exports/world.ply"): short})

    destination = tmp_path / "kitchen.ply"
    with pytest.raises(MarbleError, match="truncated"):
        client.download("https://storage.googleapis.com/wl-exports/world.ply", destination)

    assert not destination.exists()
    assert list(tmp_path.glob("*.part")) == []


def test_download_reports_progress(routed, tmp_path: Path):
    client = routed({("GET", "/wl-exports/world.ply"): httpx.Response(200, content=b"x" * 4096)})

    seen: list[int] = []
    client.download(
        "https://storage.googleapis.com/wl-exports/world.ply",
        tmp_path / "k.ply",
        on_progress=lambda written, total: seen.append(written),
        chunk=1024,
    )
    assert seen and seen[-1] == 4096


# ---------------------------------------------------------------------------------------
# Errors, and what is and is not retried
# ---------------------------------------------------------------------------------------


def test_402_says_out_of_credits_rather_than_request_failed(routed):
    client = routed({("GET", "/credits"): json_response("error_402", status=402)})
    with pytest.raises(InsufficientCredits, match="out of API credits"):
        client.credits()


def test_401_names_the_variable_to_check_and_never_the_key(routed):
    client = routed({("GET", "/credits"): httpx.Response(401, json={"error": {"message": "nope"}})})
    with pytest.raises(AuthError) as caught:
        client.credits()

    assert "WORLDLABS_API_KEY" in str(caught.value)
    assert "test-key-do-not-log" not in str(caught.value)


def test_400_carries_the_servers_own_sentence(routed):
    client = routed({("POST", "/worlds:generate"): json_response("error_400", status=400)})
    with pytest.raises(BadRequest, match="could not be fetched"):
        client.generate(text_prompt("a kitchen"), MODELS["draft"])


def test_422_is_a_bad_request_too(routed):
    client = routed({("POST", "/worlds:generate"): json_response("error_422", status=422)})
    with pytest.raises(BadRequest, match="world_prompt.type"):
        client.generate(text_prompt("a kitchen"), MODELS["draft"])


def test_404_is_its_own_error(routed):
    client = routed({("GET", "/operations/missing"): httpx.Response(404, json={})})
    with pytest.raises(NotFound):
        client.operation("missing")


def test_a_bad_request_is_never_retried(routed, recorder):
    """Retrying a 400 spends the same wrong request four times and changes nothing."""
    client = routed({("POST", "/worlds:generate"): json_response("error_400", status=400)})
    with pytest.raises(BadRequest):
        client.generate(text_prompt("a kitchen"), MODELS["draft"])

    assert len(recorder) == 1


def test_429_honours_retry_after_exactly(routed, clock, recorder):
    limited = httpx.Response(429, headers={"Retry-After": "12"}, json=fixture("error_429"))
    client = routed({("GET", "/credits"): [limited, limited, json_response("credits")]})

    assert client.credits() == 6250
    assert clock.waits == [12.0, 12.0]
    assert len(recorder) == 3


def test_429_without_a_header_backs_off_exponentially(routed, clock):
    limited = json_response("error_429", status=429)
    client = routed({("GET", "/credits"): [limited, limited, limited, json_response("credits")]})

    client.credits()
    # jitter is pinned to zero in tests, so these are the bare powers of the backoff base.
    assert clock.waits == [1.0, 2.0, 4.0]


def test_backoff_actually_uses_the_jitter(routed, clock):
    """Without jitter a fleet of clients retries in lockstep and re-creates the spike."""
    limited = json_response("error_429", status=429)
    client = routed(
        {("GET", "/credits"): [limited, json_response("credits")]},
        jitter=lambda: 0.25,
    )
    client.credits()
    assert clock.waits == [1.25]


def test_a_persistent_429_eventually_surfaces(routed, clock):
    client = routed({("GET", "/credits"): json_response("error_429", status=429)}, max_retries=2)
    with pytest.raises(RateLimited):
        client.credits()
    assert len(clock.waits) == 2


def test_a_server_error_is_retried_then_reported(routed, clock, recorder):
    client = routed({("GET", "/credits"): httpx.Response(503, text="upstream down")}, max_retries=2)
    with pytest.raises(ServerError, match="503"):
        client.credits()
    assert len(recorder) == 3


def test_a_flaky_server_error_recovers(routed):
    client = routed({("GET", "/credits"): [httpx.Response(500), json_response("credits")]})
    assert client.credits() == 6250


# ---------------------------------------------------------------------------------------
# Money
# ---------------------------------------------------------------------------------------


def test_an_estimate_covers_the_pano_as_well_as_the_model():
    assert estimate_credits(image_prompt(media_asset_id="a"), MODELS["draft"]) == 150 + 80
    assert estimate_credits(text_prompt("a kitchen"), MODELS["standard"]) == 1500 + 80


def test_the_plus_tier_is_quoted_at_its_worst_case():
    """It bills a variable second stage. Quoting the base would under-quote by half."""
    assert estimate_credits(text_prompt("x"), MODELS["plus"]) == 1500 + 1500 + 80


def test_a_panorama_input_costs_nothing_to_convert():
    pano = image_prompt(media_asset_id="a", is_pano="true")
    assert prompt_kind(pano) == "pano"
    assert estimate_credits(pano, MODELS["draft"]) == 150


def test_credits_convert_to_dollars_at_the_published_rate():
    assert usd(1250) == pytest.approx(1.0)
    assert usd(230) == pytest.approx(0.184)
