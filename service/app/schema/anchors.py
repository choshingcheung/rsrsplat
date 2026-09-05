"""Symbolic anchors and axes, resolved to coordinates.

Ported from ``akitech/splat`` ``src/anchors.py``. **This is the load-bearing design choice
in the whole system.** The schema says ``"bottom_front_edge"`` and ``"left_right"``, never
``(0.12, 0.0, -0.15)`` and ``(0, 1, 0)``. A language model naming a location on an object is
reliable; a language model guessing a number in our coordinate frame is not. So the model
names, and this module resolves the name against the object's measured half-extents.

It is also where perception enters. The half-extents come from the user's selection —
PCA over the splats they dragged a box around — so the same anchor name lands somewhere
different on a dishwasher and on a microwave, without anything above this line changing.
That is what "language supplies the mechanism, perception supplies the pose" means in code.

**Frame convention, stated once, and it is the same frame the wire protocol uses:**

    +x is FRONT, +y is LEFT, +z is UP

Right-handed, because left is ``up × front``. See ``app/protocol.py``, where the selection's
canonical axes are defined in exactly this order, and ``DECISIONS.md`` for why the contract
was changed to match this rather than the other way round.

The anchor table is defined once, here. The validator and the model prompt both read their
allowed set out of it, so a name added here appears in all three on the same commit. Three
copies of a vocabulary drift apart; one does not.
"""

from __future__ import annotations

from collections.abc import Callable

Vec3 = tuple[float, float, float]

#: A joint axis, as a direction in the child body's frame. MuJoCo does not require these to
#: be normalised, but they are.
AXIS_MAP: dict[str, Vec3] = {
    "left_right": (0.0, 1.0, 0.0),
    "front_back": (1.0, 0.0, 0.0),
    "up_down": (0.0, 0.0, 1.0),
}

#: Each entry takes the object's half-extents and returns a point in the shell's local frame.
ANCHOR_MAP: dict[str, Callable[[float, float, float], Vec3]] = {
    # Edges of the front face -- where hinges live.
    "bottom_front_edge": lambda hx, hy, hz: (hx, 0.0, -hz),
    "top_front_edge": lambda hx, hy, hz: (hx, 0.0, hz),
    # +y is LEFT. The prototype had these two the wrong way round: harmless on a symmetric
    # box, which is why it survived, but it puts a side-hinged door's hinge on the wrong
    # edge. See PORTING.md hazard 3.
    "left_front_edge": lambda hx, hy, hz: (hx, hy, 0.0),
    "right_front_edge": lambda hx, hy, hz: (hx, -hy, 0.0),
    # Inside the shell -- where trays and racks live.
    "interior_lower": lambda hx, hy, hz: (0.0, 0.0, -hz * 0.4),
    "interior_upper": lambda hx, hy, hz: (0.0, 0.0, hz * 0.4),
    # On the front face -- where buttons live.
    "front_panel_upper_right": lambda hx, hy, hz: (hx, -hy * 0.6, hz * 0.75),
    "front_panel_center": lambda hx, hy, hz: (hx, 0.0, hz * 0.75),
    # The object's own centre, for a part that is the whole thing.
    "centre": lambda hx, hy, hz: (0.0, 0.0, 0.0),
}

ANCHOR_NAMES = frozenset(ANCHOR_MAP)
AXIS_NAMES = frozenset(AXIS_MAP)


def resolve_axis(name: str) -> Vec3:
    if name not in AXIS_MAP:
        raise ValueError(f"unknown axis {name!r}. known: {', '.join(sorted(AXIS_MAP))}")
    return AXIS_MAP[name]


def resolve_anchor(name: str, half: Vec3) -> Vec3:
    """A symbolic anchor to a position in the shell's local frame.

    ``half`` is the object's half-extents (hx, hy, hz). The returned point is where the child
    body's origin goes — and since joints anchor at ``pos="0 0 0"`` inside that body, it is
    also the pivot. Getting this wrong is the difference between a door that swings on its
    bottom edge and one that swings about its centre like a helicopter blade.
    """
    if name not in ANCHOR_MAP:
        raise ValueError(f"unknown anchor {name!r}. known: {', '.join(sorted(ANCHOR_MAP))}")
    hx, hy, hz = half
    return ANCHOR_MAP[name](hx, hy, hz)
