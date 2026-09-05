"""Articulation schemas: the vocabulary, the resolvers, and hard validation.

A model writes a schema from the user's sentence; ``app/mjcf`` turns it into MJCF. The
schema is symbolic throughout — it names anchors and axes, never coordinates — because a
model naming a location on an object is reliable and a model guessing a number in our
coordinate frame is not.

``fallbacks/`` holds stored schemas for when generation fails or there is no network. They
are loaded before the model path is ever built, because that is what survives a bad venue.
"""

from pathlib import Path

from .anchors import (
    ANCHOR_MAP,
    ANCHOR_NAMES,
    AXIS_MAP,
    AXIS_NAMES,
    resolve_anchor,
    resolve_axis,
)
from .validate import (
    DEFAULT_MOBILITY,
    DEFAULT_SHAPE,
    MAX_HINGE_DEGREES,
    MAX_OBJECT_METRES,
    MAX_SLIDE_METRES,
    VALID_ACTIONS,
    VALID_ANCHORS,
    VALID_AXES,
    VALID_JOINTS,
    VALID_MOBILITY,
    VALID_OPS,
    VALID_SHAPES,
    half_extents,
    mobility,
    parts,
    rest_position,
    shape,
    validate,
    with_measured_frame,
)

#: Stored schemas, loaded when generation fails or there is no network.
FALLBACK_DIR = Path(__file__).parent / "fallbacks"

#: The last resort: a solid thing that falls and settles. Deliberately unremarkable — it
#: must never itself become the interesting failure.
DEFAULT_FALLBACK = FALLBACK_DIR / "_default.json"

__all__ = [
    "ANCHOR_MAP",
    "ANCHOR_NAMES",
    "AXIS_MAP",
    "AXIS_NAMES",
    "DEFAULT_FALLBACK",
    "DEFAULT_MOBILITY",
    "DEFAULT_SHAPE",
    "FALLBACK_DIR",
    "MAX_HINGE_DEGREES",
    "MAX_OBJECT_METRES",
    "MAX_SLIDE_METRES",
    "VALID_ACTIONS",
    "VALID_ANCHORS",
    "VALID_AXES",
    "VALID_JOINTS",
    "VALID_MOBILITY",
    "VALID_OPS",
    "VALID_SHAPES",
    "half_extents",
    "mobility",
    "parts",
    "resolve_anchor",
    "resolve_axis",
    "rest_position",
    "shape",
    "validate",
    "with_measured_frame",
]
