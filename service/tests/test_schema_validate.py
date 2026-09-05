"""Validation has to catch what a model actually gets wrong, not what is easy to check.

Ported from ``akitech/splat`` ``tests/test_schema.py``, plus coverage for the three
rsrsplat additions: ``mobility``, ``shape``, and objects with no parts at all.
"""

from __future__ import annotations

import copy
import json

import pytest

from app.schema import FALLBACK_DIR, half_extents, mobility, shape, validate, with_measured_frame

REFERENCE = FALLBACK_DIR / "dishwasher.json"


@pytest.fixture
def schema() -> dict:
    return json.loads(REFERENCE.read_text(encoding="utf-8"))


def only(errors: list[str], needle: str) -> str:
    """Assert exactly one error mentions ``needle``, and return it."""
    hits = [e for e in errors if needle in e]
    assert len(hits) == 1, f"expected one error mentioning {needle!r}, got {errors}"
    return hits[0]


# ---- every stored fallback must be usable ------------------------------------------------


@pytest.mark.parametrize("path", sorted(FALLBACK_DIR.glob("*.json")), ids=lambda p: p.stem)
def test_every_stored_fallback_validates(path):
    """These are what stands in when there is no network. A broken one is a dead demo."""
    assert validate(json.loads(path.read_text(encoding="utf-8"))) == []


def test_the_reference_schema_validates(schema):
    assert validate(schema) == []


def test_half_extents_are_half(schema):
    assert half_extents(schema) == pytest.approx((0.12, 0.115, 0.15))


# ---- the rsrsplat additions ---------------------------------------------------------------


def test_an_object_with_no_parts_is_valid():
    """rsrsplat's most common case: "a wooden crate, heavy". No articulation, still an object.

    The prototype required a non-empty parts list, because it only ever handled appliances.
    """
    assert validate(json.loads((FALLBACK_DIR / "crate.json").read_text(encoding="utf-8"))) == []


def test_parts_may_be_absent_entirely():
    bare = {"object": "rock", "mobility": "free", "frame": {"size": [0.2, 0.2, 0.2]}}
    assert validate(bare) == []


def test_mobility_defaults_to_fixed_and_shape_to_box():
    """Defaults are the conservative choice: welded, and solid. A thing that unexpectedly
    falls through the floor is a worse first impression than a thing that sits still."""
    bare = {"object": "thing", "frame": {"size": [0.2, 0.2, 0.2]}}
    assert validate(bare) == []
    assert mobility(bare) == "fixed"
    assert shape(bare) == "box"


def test_an_invented_mobility_is_rejected():
    bad = {"object": "thing", "mobility": "floaty", "frame": {"size": [0.2, 0.2, 0.2]}}
    assert "allowed: fixed, free" in only(validate(bad), "bad mobility")


def test_an_invented_shape_is_rejected():
    bad = {"object": "thing", "shape": "blob", "frame": {"size": [0.2, 0.2, 0.2]}}
    assert "allowed: box, shell" in only(validate(bad), "bad shape")


def test_an_interior_part_inside_a_solid_box_is_rejected(schema):
    """A rack needs a cavity to slide into. MuJoCo would happily build this and the tray
    would live inside solid geometry, which looks like a physics bug and is not one."""
    schema["shape"] = "box"
    err = only(validate(schema), "inside the shell")
    assert "shape 'shell'" in err, "the error must say how to fix it"


def test_the_measured_frame_replaces_whatever_the_model_guessed(schema):
    """Language supplies the mechanism, perception supplies the pose."""
    out = with_measured_frame(schema, (0.30, 0.30, 0.425), origin=(1.2, -0.4, -0.995))
    assert out["frame"]["size"] == [0.60, 0.60, 0.85]
    assert out["frame"]["origin"] == [1.2, -0.4, -0.995]
    assert half_extents(out) == pytest.approx((0.30, 0.30, 0.425))
    assert validate(out) == []
    assert schema["frame"]["size"] == [0.24, 0.23, 0.30], "the original must not be mutated"


# ---- what a model gets wrong --------------------------------------------------------------


def test_not_an_object():
    assert validate([1, 2, 3]) == ["schema must be a JSON object, got list"]


def test_duplicate_part_name(schema):
    schema["parts"].append(copy.deepcopy(schema["parts"][0]))
    only(validate(schema), "duplicate part name 'door'")


def test_invented_anchor(schema):
    schema["parts"][0]["anchor"] = "hinge_side_thing"
    err = only(validate(schema), "bad anchor")
    # The message must name the allowed set, because it is what gets fed back on a retry.
    assert "bottom_front_edge" in err


def test_invented_joint_type(schema):
    schema["parts"][0]["joint"] = "revolute"
    assert "bad joint 'revolute'" in only(validate(schema), "bad joint")


def test_invented_axis(schema):
    schema["parts"][1]["axis"] = "in_out"
    only(validate(schema), "bad axis")


def test_inverted_range(schema):
    schema["parts"][1]["range"] = [0.18, 0.0]
    only(validate(schema), "inverted")


def test_hinge_range_in_radians_is_caught_as_implausible(schema):
    """The realistic failure is the opposite way round: 1.57 is fine, 1570 is not."""
    schema["parts"][0]["range"] = [0, 400]
    assert "DEGREES" in only(validate(schema), "hinge range")


def test_slide_range_in_millimetres_is_caught(schema):
    schema["parts"][1]["range"] = [0, 180]  # meant mm, said m
    assert "METRES" in only(validate(schema), "travel")


def test_requires_naming_a_part_that_does_not_exist(schema):
    schema["parts"][1]["requires"] = ["hatch >= 80"]
    assert "known:" in only(validate(schema), "unknown part 'hatch'")


def test_requires_malformed(schema):
    schema["parts"][1]["requires"] = ["door open"]
    only(validate(schema), "malformed")


def test_requires_bad_operator(schema):
    schema["parts"][1]["requires"] = ["door => 80"]
    only(validate(schema), "bad operator")


def test_requires_non_numeric_threshold(schema):
    schema["parts"][1]["requires"] = ["door >= wide"]
    only(validate(schema), "is not a number")


def test_affordance_with_an_unknown_action(schema):
    schema["parts"][0]["affordances"][0]["action"] = "yank"
    only(validate(schema), "bad affordance action")


def test_affordance_missing_a_site(schema):
    del schema["parts"][0]["affordances"][0]["site"]
    only(validate(schema), "missing a 'site'")


def test_missing_frame(schema):
    del schema["frame"]
    only(validate(schema), "missing 'frame'")


def test_object_sized_like_a_room(schema):
    schema["frame"]["size"] = [4.0, 0.23, 0.30]
    only(validate(schema), "implausible for an object")


def test_a_fixed_part_needs_no_axis_or_range(schema):
    schema["parts"].append({"name": "trim", "joint": "fixed", "anchor": "front_panel_center"})
    assert validate(schema) == []


def test_validation_never_raises_on_garbage():
    """A missing key is a finding to feed back, never a crash on the model's output."""
    for garbage in ({}, {"parts": [{}]}, {"parts": [{"name": "x"}]}, {"object": "x"}, {"parts": 3}):
        assert isinstance(validate(garbage), list)


# ---- preconditions that can never be met ---------------------------------------------------


def test_a_precondition_on_a_sprung_button_is_rejected(schema):
    """Found live on a briefcase described as ``lid requires latch <= -0.003``.

    Semantically reasonable — a latch really does hold a lid shut — but a button springs
    back to rest the moment it is released, so the lid could never open. It compiled, passed
    every other check, and was a dead twin.
    """
    schema["parts"][0]["requires"] = ["start_button <= -0.005"]
    err = only(validate(schema), "can never be satisfied")
    assert "sprung button" in err
    assert "returns to 0" in err


def test_a_button_precondition_that_rest_already_satisfies_is_fine(schema):
    schema["parts"][0]["requires"] = ["start_button >= -0.001"]
    assert validate(schema) == []


def test_a_requires_closed_precondition_is_valid(schema):
    """``door <= 5`` guards against a part being OPEN, is satisfied at rest, and is correct —
    a washing machine will not start with its door open."""
    schema["parts"][2]["requires"] = ["door <= 5"]
    assert validate(schema) == []


def test_preconditions_on_hinges_and_slides_are_never_flagged(schema):
    """They stay where they are put, so any threshold inside their range is reachable."""
    schema["parts"][1]["requires"] = ["door >= 179"]
    assert validate(schema) == []


def test_rest_position_is_the_end_nearer_zero():
    from app.schema import rest_position

    assert rest_position({"range": [-0.006, 0]}) == 0
    assert rest_position({"range": [0, 90]}) == 0
    assert rest_position({"range": [-0.02, 0]}) == 0
