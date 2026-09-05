"""The resolver is the seam between language and geometry, so it gets checked properly.

Ported from ``akitech/splat`` ``tests/test_anchors.py``, with the left/right expectations
corrected — see PORTING.md hazard 3 and the note in ``anchors.py``.
"""

from __future__ import annotations

import pytest

from app.schema import ANCHOR_MAP, ANCHOR_NAMES, AXIS_MAP, resolve_anchor, resolve_axis

HALF = (0.12, 0.115, 0.15)  # the reference dishwasher


def test_every_declared_anchor_resolves():
    for name in ANCHOR_NAMES:
        point = resolve_anchor(name, HALF)
        assert len(point) == 3
        assert all(isinstance(v, float) for v in point)


def test_an_unknown_anchor_raises_and_lists_the_known_ones():
    with pytest.raises(ValueError) as exc:
        resolve_anchor("hinge_side_thing", HALF)
    # The message is what the retry loop feeds back, so it must carry the vocabulary.
    assert "bottom_front_edge" in str(exc.value)


def test_an_unknown_axis_raises():
    with pytest.raises(ValueError, match="unknown axis"):
        resolve_axis("in_out")


def test_the_door_anchor_is_the_bottom_front_edge():
    """Front is +x and up is +z, so it must be at max x and min z, centred in y."""
    assert resolve_anchor("bottom_front_edge", HALF) == pytest.approx((0.12, 0.0, -0.15))


def test_front_and_back_edges_differ_only_in_height():
    bottom = resolve_anchor("bottom_front_edge", HALF)
    top = resolve_anchor("top_front_edge", HALF)
    assert bottom[0] == top[0] and bottom[1] == top[1]
    assert bottom[2] == -top[2]


def test_left_is_positive_y_and_right_is_negative_y():
    """The correction to the prototype, and the reason it is worth a test of its own.

    In a right-handed frame with +x front and +z up, +y is LEFT — it is ``up × front``. The
    prototype had these two swapped, which is invisible on a symmetric box and puts a
    side-hinged door's hinge on the wrong edge of a real one.
    """
    left = resolve_anchor("left_front_edge", HALF)
    right = resolve_anchor("right_front_edge", HALF)
    assert left[1] == pytest.approx(+HALF[1])
    assert right[1] == pytest.approx(-HALF[1])
    assert left[1] == -right[1] != 0.0


def test_the_right_hand_panel_anchor_is_on_the_right():
    _, y, _ = resolve_anchor("front_panel_upper_right", HALF)
    assert y < 0, "right is -y, so a 'right' panel anchor must be at negative y"


def test_interior_anchors_are_inside_the_box():
    for name in ("interior_lower", "interior_upper"):
        x, y, z = resolve_anchor(name, HALF)
        assert abs(x) < HALF[0] and abs(y) < HALF[1] and abs(z) < HALF[2]


def test_panel_anchors_are_on_the_front_face():
    for name in ("front_panel_center", "front_panel_upper_right"):
        x, _, z = resolve_anchor(name, HALF)
        assert x == pytest.approx(HALF[0])
        assert z > 0, "an 'upper' anchor must be in the top half"


def test_centre_is_the_origin_for_an_object_that_is_all_one_part():
    assert resolve_anchor("centre", HALF) == (0.0, 0.0, 0.0)


def test_anchors_scale_with_the_object():
    """Nothing may be hardcoded to dishwasher dimensions — the same names run on a microwave,
    and in rsrsplat they run on whatever the user's selection happened to measure."""
    small = (0.06, 0.05, 0.04)
    for name in ANCHOR_MAP:
        big_pt = resolve_anchor(name, HALF)
        small_pt = resolve_anchor(name, small)
        for i in range(3):
            assert abs(small_pt[i]) <= abs(big_pt[i]) + 1e-12


def test_axes_are_unit_vectors_along_one_axis_each():
    seen = set()
    for name, axis in AXIS_MAP.items():
        assert sum(abs(v) for v in axis) == pytest.approx(1.0), name
        seen.add(axis)
    assert len(seen) == 3, "the three axes must be distinct"


def test_left_right_is_the_hinge_axis_sign_that_was_measured():
    """The prototype's ``check_joints.py`` proved (0, 1, 0) swings the door forward and
    (0, -1, 0) sends it backwards through the shell. Pinned so nobody tidies it later.

    Note this is NOT affected by the left/right label correction above: the sign was
    established by watching a door swing, not by reading the name.
    """
    assert AXIS_MAP["left_right"] == (0.0, 1.0, 0.0)
