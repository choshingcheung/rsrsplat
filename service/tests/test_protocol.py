"""The seam test.

``service/app/protocol.py`` and ``web/src/types/protocol.ts`` are maintained by hand, and
hand-mirrored files drift. ``contract/fixtures/`` holds one golden JSON message per type;
this file is the Python half of the guard, and ``web/src/types/protocol.fixtures.test.ts``
is the TypeScript half. Change the seam on one side without the other and one of the two
goes red.

The most important test here is :func:`test_every_message_type_has_a_fixture`. Adding a
message type without a fixture leaves a hole in the guard, so it is an error.
"""

from __future__ import annotations

import json
import math
import pathlib
from typing import Any, Union, get_args, get_origin

import pytest
from pydantic import TypeAdapter, ValidationError

from app.protocol import ClientMessage, ServerMessage

FIXTURES = pathlib.Path(__file__).resolve().parents[2] / "contract" / "fixtures"

CLIENT_ADAPTER: TypeAdapter[Any] = TypeAdapter(ClientMessage)
SERVER_ADAPTER: TypeAdapter[Any] = TypeAdapter(ServerMessage)


def _fixture_paths(direction: str) -> list[pathlib.Path]:
    return sorted((FIXTURES / direction).glob("*.json"))


def _load(path: pathlib.Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _declared_type_tags(union: Any) -> set[str]:
    """The ``type`` literal of every member of an Annotated discriminated union."""
    annotated_args = get_args(union)
    members = get_args(annotated_args[0]) if get_origin(annotated_args[0]) is Union else ()
    return {model.model_fields["type"].default for model in members}


CLIENT_PATHS = _fixture_paths("client")
SERVER_PATHS = _fixture_paths("server")


def test_fixtures_directory_is_not_empty() -> None:
    """A silently empty fixture directory would make every test below vacuously pass."""
    assert CLIENT_PATHS, f"no client fixtures under {FIXTURES / 'client'}"
    assert SERVER_PATHS, f"no server fixtures under {FIXTURES / 'server'}"


@pytest.mark.parametrize("path", CLIENT_PATHS, ids=lambda p: p.stem)
def test_client_fixture_validates(path: pathlib.Path) -> None:
    message = CLIENT_ADAPTER.validate_python(_load(path))
    assert message.type == path.stem, "fixture filename must match its type tag"


@pytest.mark.parametrize("path", SERVER_PATHS, ids=lambda p: p.stem)
def test_server_fixture_validates(path: pathlib.Path) -> None:
    message = SERVER_ADAPTER.validate_python(_load(path))
    assert message.type == path.stem, "fixture filename must match its type tag"


@pytest.mark.parametrize(
    ("path", "adapter"),
    [(p, CLIENT_ADAPTER) for p in CLIENT_PATHS] + [(p, SERVER_ADAPTER) for p in SERVER_PATHS],
    ids=lambda value: value.stem if isinstance(value, pathlib.Path) else "",
)
def test_fixture_round_trips_byte_for_byte(path: pathlib.Path, adapter: TypeAdapter[Any]) -> None:
    """Serialising a parsed fixture must reproduce the fixture.

    This is what catches a field renamed on one side only, or a camelCase alias that was
    forgotten -- the failure mode a schema check alone would wave through.
    """
    original = _load(path)
    message = adapter.validate_python(original)
    assert message.model_dump(by_alias=True, mode="json") == original


def test_every_message_type_has_a_fixture() -> None:
    """No message type may exist without a golden example.

    If this fails, either add the fixture or remove the type. A message with no fixture is
    a hole in the guard that keeps the two protocol files in step.
    """
    assert _declared_type_tags(ClientMessage) == {p.stem for p in CLIENT_PATHS}
    assert _declared_type_tags(ServerMessage) == {p.stem for p in SERVER_PATHS}


def test_unknown_fields_are_rejected() -> None:
    """A typo in a hand-mirrored contract should fail on the first message, not be dropped."""
    payload = _load(FIXTURES / "client" / "sim.control.json") | {"speed": 2.0}
    with pytest.raises(ValidationError):
        CLIENT_ADAPTER.validate_python(payload)


def test_selection_axes_are_right_handed() -> None:
    """A left-handed PCA frame is a reflection, not a rotation, and mirrors everything after it.

    The client is responsible for the determinant check; this asserts the golden example
    actually demonstrates the convention it documents.
    """
    selection = CLIENT_ADAPTER.validate_python(
        _load(FIXTURES / "client" / "selection.commit.json")
    ).selection
    a = selection.axes
    # Column-major: columns are (a[0:3]), (a[3:6]), (a[6:9]).
    det = (
        a[0] * (a[4] * a[8] - a[5] * a[7])
        - a[3] * (a[1] * a[8] - a[2] * a[7])
        + a[6] * (a[1] * a[5] - a[2] * a[4])
    )
    assert det == pytest.approx(1.0, abs=1e-9), "axes must be right-handed and orthonormal"


def test_wire_quaternions_are_unit_and_w_first() -> None:
    """(w, x, y, z), MuJoCo convention. A (x, y, z, w) fixture would sail past a length check
    alone, so assert the scalar part is where it is claimed to be as well."""
    batch = SERVER_ADAPTER.validate_python(_load(FIXTURES / "server" / "pose.batch.json"))
    for pose in batch.poses:
        assert math.isclose(sum(c * c for c in pose.orientation), 1.0, abs_tol=1e-6)
    shell = batch.poses[0].orientation
    # A 30 degree yaw: scalar part is cos(15 degrees), and the vector part is z-only.
    assert shell[0] == pytest.approx(math.cos(math.radians(15.0)))
    assert shell[1] == pytest.approx(0.0)
    assert shell[2] == pytest.approx(0.0)


def test_hinge_anchor_sits_on_the_bottom_front_edge() -> None:
    """The canonical frame is column 0 = +front, column 1 = +left, column 2 = +up.

    A bottom-hinged door's body origin sits ON its hinge anchor, so the golden example's
    door position must equal the centroid pushed one half-depth forward and one half-height
    down. If anyone swaps the frame's columns, this is the assertion that says so — and it
    is worth pinning, because the mismatch it guards against is invisible in a viewer: the
    door would swing, just carrying the wrong quarter of the object.
    """
    selection = CLIENT_ADAPTER.validate_python(
        _load(FIXTURES / "client" / "selection.commit.json")
    ).selection
    created = SERVER_ADAPTER.validate_python(_load(FIXTURES / "server" / "object.created.json"))
    door = next(p for p in created.object.parts if p.joint_type == "hinge")

    a, c, h = selection.axes, selection.centroid, selection.half_extents
    front, up = a[0:3], a[6:9]
    expected = [c[i] + h[0] * front[i] - h[2] * up[i] for i in range(3)]

    assert list(door.initial_pose.position) == pytest.approx(expected, abs=1e-9)
    assert door.splat_subset == "front_lower"


def test_hinge_range_is_in_degrees_not_radians() -> None:
    """Ranges cross the socket in degrees. A radian range would be numerically small; a
    door that opens 90 must read as 90."""
    created = SERVER_ADAPTER.validate_python(_load(FIXTURES / "server" / "object.created.json"))
    door = next(p for p in created.object.parts if p.joint_type == "hinge")
    assert door.range == (0.0, 90.0)


def test_part_without_explicit_subset_defaults_to_the_whole_selection() -> None:
    payload = {
        "bodyName": "crate",
        "jointType": "free",
        "range": None,
        "initialPose": {
            "bodyName": "crate",
            "position": [0.0, 0.0, 0.0],
            "orientation": [1.0, 0.0, 0.0, 0.0],
        },
    }
    from app.protocol import PhysicsPart

    assert PhysicsPart.model_validate(payload).splat_subset == "all"
