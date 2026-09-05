"""The service, end to end, through a real WebSocket.

S6's acceptance criterion in one sentence: a client connects, sends a selection and a
physicalize, receives ``object.created``, and then watches a stream of ``pose.batch``
messages showing a body fall under gravity and come to rest.

Everything here goes through the wire format. A test that reached into the session directly
would pass while the socket was sending something the browser could not parse.
"""

from __future__ import annotations

import json
import pathlib

import pytest
from fastapi.testclient import TestClient
from pydantic import TypeAdapter

from app.main import app
from app.protocol import ServerMessage

FIXTURES = pathlib.Path(__file__).resolve().parents[2] / "contract" / "fixtures"
SERVER = TypeAdapter(ServerMessage)
GROUND = -1.42


def fixture(name: str) -> dict:
    return json.loads((FIXTURES / "client" / f"{name}.json").read_text(encoding="utf-8"))


def scene_load(ground: float = GROUND) -> dict:
    payload = fixture("scene.load")
    payload["world"] = {"up": [0.0, 0.0, 1.0], "groundHeight": ground, "sceneScale": 1.0}
    return payload


def selection_commit(
    sel_id: str = "sel_01",
    centroid=(0.0, 0.0, GROUND + 1.0),
    half=(0.25, 0.25, 0.2),
) -> dict:
    return {
        "type": "selection.commit",
        "selection": {
            "id": sel_id,
            "splatCount": 1000,
            "centroid": list(centroid),
            "axes": [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0],
            "halfExtents": list(half),
        },
    }


def recv(ws):
    """One server message, parsed and validated against the contract."""
    return SERVER.validate_json(ws.receive_text())


def recv_until(ws, wanted: str, limit: int = 200):
    """Skip the pose stream until the message we are actually waiting for arrives."""
    for _ in range(limit):
        message = recv(ws)
        if message.type == wanted:
            return message
    raise AssertionError(f"no {wanted} within {limit} messages")


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


# ---- the plumbing --------------------------------------------------------------------------


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_loading_a_scene_is_acknowledged_before_anything_else_happens(client):
    """The client must not race session setup. That is what scene.ready is for."""
    with client.websocket_connect("/ws") as ws:
        ws.send_json(scene_load())
        ready = recv(ws)
        assert ready.type == "scene.ready"
        assert ready.session_id.startswith("ses_")
        assert recv(ws).type == "sim.status"


def test_a_scene_the_service_cannot_simulate_is_refused_with_a_reason(client):
    """A tilted capture would otherwise run a session in which gravity points at a wall."""
    with client.websocket_connect("/ws") as ws:
        payload = scene_load()
        payload["world"]["up"] = [0.0, 0.9, 0.44]
        ws.send_json(payload)
        failed = recv(ws)
        assert failed.type == "object.failed"
        assert "z-up" in failed.reason


def test_a_malformed_message_does_not_take_the_session_down(client):
    """One bad frame from the other side of a hand-mirrored contract is a bug to log, not a
    reason to drop a scene the user has been working in."""
    with client.websocket_connect("/ws") as ws:
        ws.send_json(scene_load())
        recv_until(ws, "scene.ready")
        ws.send_text('{"type": "scene.load", "splatId": 5}')
        ws.send_json(selection_commit())
        ws.send_json({"type": "object.physicalize", "selectionId": "sel_01", "prompt": "a crate"})
        assert recv_until(ws, "object.created").object.id.startswith("obj_")


# ---- physicalize ---------------------------------------------------------------------------


def test_physicalizing_before_a_scene_exists_is_refused(client):
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "object.physicalize", "selectionId": "sel_01", "prompt": "a crate"})
        failed = recv(ws)
        assert failed.type == "object.failed"
        assert "no scene loaded" in failed.reason


def test_physicalizing_a_selection_that_was_never_committed_is_refused(client):
    with client.websocket_connect("/ws") as ws:
        ws.send_json(scene_load())
        recv_until(ws, "scene.ready")
        ws.send_json({"type": "object.physicalize", "selectionId": "ghost", "prompt": "a crate"})
        failed = recv_until(ws, "object.failed")
        assert "unknown selection" in failed.reason
        assert "commit it first" in failed.reason


def test_a_crate_becomes_a_free_body_with_a_readable_label(client):
    with client.websocket_connect("/ws") as ws:
        ws.send_json(scene_load())
        recv_until(ws, "scene.ready")
        ws.send_json(selection_commit())
        ws.send_json(
            {
                "type": "object.physicalize",
                "selectionId": "sel_01",
                "prompt": "a wooden crate, heavy, sits flat",
            }
        )
        created = recv_until(ws, "object.created").object

    assert created.selection_id == "sel_01"
    assert created.label == "crate"
    assert created.parts[0].joint_type == "free"
    assert created.mass_kg > 0


def test_a_dishwasher_comes_back_with_a_hinge_in_degrees(client):
    """The offline path already knows what a dishwasher is. This is the demo's second half
    working with the network unplugged."""
    with client.websocket_connect("/ws") as ws:
        ws.send_json(scene_load())
        recv_until(ws, "scene.ready")
        ws.send_json(selection_commit(half=(0.30, 0.30, 0.425)))
        ws.send_json(
            {
                "type": "object.physicalize",
                "selectionId": "sel_01",
                "prompt": "a dishwasher, the door hinges at the bottom and opens ninety degrees",
            }
        )
        created = recv_until(ws, "object.created").object

    door = next(p for p in created.parts if p.joint_type == "hinge")
    assert door.range == (0.0, 90.0), "degrees on the wire, never radians"
    assert door.splat_subset != "all", "the door must own a subset, not the whole selection"
    assert created.parts[0].splat_subset == "all", "the shell is the fallback, and comes first"


def test_every_part_carries_the_pose_the_client_needs_to_bind_splats_to(client):
    with client.websocket_connect("/ws") as ws:
        ws.send_json(scene_load())
        recv_until(ws, "scene.ready")
        ws.send_json(selection_commit(half=(0.30, 0.30, 0.425)))
        ws.send_json(
            {"type": "object.physicalize", "selectionId": "sel_01", "prompt": "a dishwasher"}
        )
        created = recv_until(ws, "object.created").object

    for part in created.parts:
        assert part.initial_pose.body_name == part.body_name
        assert len(part.initial_pose.orientation) == 4
        assert sum(c * c for c in part.initial_pose.orientation) == pytest.approx(1.0, abs=1e-6)


# ---- the stream: S6's acceptance criterion --------------------------------------------------


def test_a_physicalized_body_falls_under_gravity_and_comes_to_rest(client):
    """The whole loop, over a real socket: connect, select, describe, watch it land."""
    with client.websocket_connect("/ws") as ws:
        ws.send_json(scene_load())
        recv_until(ws, "scene.ready")
        ws.send_json(selection_commit(centroid=(0.0, 0.0, GROUND + 1.0)))
        ws.send_json(
            {"type": "object.physicalize", "selectionId": "sel_01", "prompt": "a heavy crate"}
        )
        created = recv_until(ws, "object.created").object
        shell = created.parts[0].body_name
        start = created.parts[0].initial_pose.position[2]

        heights = []
        for _ in range(150):  # ~5 s at 30 Hz
            message = recv(ws)
            if message.type != "pose.batch":
                continue
            pose = next(p for p in message.poses if p.body_name == shell)
            heights.append(pose.position[2])
            if len(heights) > 40 and abs(heights[-1] - heights[-20]) < 1e-4:
                break

    assert len(heights) > 20, "the stream must actually deliver batches"
    assert min(heights) < start - 0.3, "it must visibly fall"
    assert heights[-1] == pytest.approx(heights[-20], abs=1e-3), "and then come to rest"
    assert heights[-1] > GROUND, "on the floor, not through it"
    assert heights[-1] == pytest.approx(GROUND + 0.2, abs=0.02), "resting on its own half-height"


def test_the_stream_carries_simulated_time_that_advances(client):
    with client.websocket_connect("/ws") as ws:
        ws.send_json(scene_load())
        recv_until(ws, "scene.ready")
        ws.send_json(selection_commit())
        ws.send_json({"type": "object.physicalize", "selectionId": "sel_01", "prompt": "a crate"})
        recv_until(ws, "object.created")

        times = [recv_until(ws, "pose.batch").t for _ in range(10)]

    assert times == sorted(times)
    assert times[-1] > times[0], "simulated time must advance between batches"


def test_nothing_is_streamed_before_anything_is_physicalized(client):
    """An empty scene has no bodies, so there is nothing to say. Sending empty batches at
    30 Hz would be noise on the wire and a flicker in any UI counting messages."""
    with client.websocket_connect("/ws") as ws:
        ws.send_json(scene_load())
        recv_until(ws, "scene.ready")
        ws.send_json({"type": "sim.control", "action": "pause"})
        assert recv(ws).type == "sim.status"
        ws.send_json({"type": "sim.control", "action": "play"})
        assert recv(ws).type == "sim.status", "only status, never an empty pose.batch"


# ---- control ---------------------------------------------------------------------------------


def test_pausing_freezes_the_scene_and_playing_resumes_it(client):
    with client.websocket_connect("/ws") as ws:
        ws.send_json(scene_load())
        recv_until(ws, "scene.ready")
        ws.send_json(selection_commit())
        ws.send_json({"type": "object.physicalize", "selectionId": "sel_01", "prompt": "a crate"})
        recv_until(ws, "object.created")

        ws.send_json({"type": "sim.control", "action": "pause"})
        status = recv_until(ws, "sim.status")
        assert status.running is False

        frozen = [recv_until(ws, "pose.batch").poses[0].position[2] for _ in range(5)]
        assert frozen[0] == pytest.approx(frozen[-1], abs=1e-9), "paused means paused"

        ws.send_json({"type": "sim.control", "action": "play"})
        assert recv_until(ws, "sim.status").running is True
        moving = [recv_until(ws, "pose.batch").poses[0].position[2] for _ in range(10)]
        assert moving[-1] < moving[0], "and playing resumes the fall"


def test_reset_puts_everything_back_where_it_started(client):
    with client.websocket_connect("/ws") as ws:
        ws.send_json(scene_load())
        recv_until(ws, "scene.ready")
        ws.send_json(selection_commit())
        ws.send_json({"type": "object.physicalize", "selectionId": "sel_01", "prompt": "a crate"})
        start = recv_until(ws, "object.created").object.parts[0].initial_pose.position[2]

        for _ in range(10):
            recv_until(ws, "pose.batch")

        # Pause first. The stream keeps advancing on a wall clock, so a reset while running
        # is already a frame of free fall behind by the time the next batch is sampled --
        # which is correct behaviour, and makes an exact assertion a race.
        ws.send_json({"type": "sim.control", "action": "pause"})
        recv_until(ws, "sim.status")
        ws.send_json({"type": "sim.control", "action": "reset"})
        recv_until(ws, "sim.status")

        after = recv_until(ws, "pose.batch")
        assert after.poses[0].position[2] == pytest.approx(start, abs=1e-9)
        assert after.t == 0.0, "reset returns simulated time to zero as well"


def test_removing_an_object_takes_it_out_of_the_stream(client):
    with client.websocket_connect("/ws") as ws:
        ws.send_json(scene_load())
        recv_until(ws, "scene.ready")
        ws.send_json(selection_commit())
        ws.send_json({"type": "object.physicalize", "selectionId": "sel_01", "prompt": "a crate"})
        created = recv_until(ws, "object.created").object

        ws.send_json({"type": "object.remove", "objectId": created.id})
        recv_until(ws, "sim.status")
        ws.send_json({"type": "sim.control", "action": "pause"})
        assert recv_until(ws, "sim.status").running is False


def test_two_objects_are_both_streamed(client):
    with client.websocket_connect("/ws") as ws:
        ws.send_json(scene_load())
        recv_until(ws, "scene.ready")
        for i, at in enumerate([(0.0, 0.0, GROUND + 1.0), (2.0, 0.0, GROUND + 1.0)]):
            ws.send_json(selection_commit(sel_id=f"sel_{i}", centroid=at))
            ws.send_json(
                {"type": "object.physicalize", "selectionId": f"sel_{i}", "prompt": "a crate"}
            )
            recv_until(ws, "object.created")

        batch = recv_until(ws, "pose.batch")
        assert len({p.body_name for p in batch.poses}) == 2
