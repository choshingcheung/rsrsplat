"""The articulation schema: the vocabulary, and hard validation against it.

Ported from ``akitech/splat`` ``src/schema.py``, with three changes for rsrsplat, recorded
at the bottom of this docstring.

This is the contract between language and geometry. A model writes one of these from the
user's sentence; ``app/mjcf`` turns it into MJCF. Everything the model can get wrong is a
field with a check here, and the error strings this module returns are exactly what gets fed
back to the model on a retry — so they are written to be read *by a model*, naming the bad
value and the allowed set.

**The vocabulary is deliberately tiny.** Nearly every household mechanism is one of four
things: a door or lid (hinge), a drawer or tray (slide), a button or switch (sprung slide),
or a fixed panel (no joint at all). Four primitives with a handful of numbers each is what
makes generation tractable where "infer arbitrary physics" is not.

Changes from the prototype, all three because rsrsplat physicalises whatever the user
selected rather than only appliances:

1. **``mobility``** — ``"free"`` or ``"fixed"``, describing the shell itself. The prototype
   only ever articulated objects bolted in place, so it had no way to say "this crate falls".
   That is rsrsplat's single most common case.
2. **``shape``** — ``"box"`` or ``"shell"``. The prototype generated a five-walled hollow
   appliance for everything. A crate is a solid box, and a solid box that silently becomes
   a hollow one looks entirely plausible in a viewer.
3. **``parts`` may be empty.** A crate has no articulation and is still a valid object.

**The frame is measured, not invented.** ``frame.size`` comes from the user's selection —
PCA half-extents doubled — and is written into the schema before generation, not asked of
the model. See :func:`with_measured_frame`. A model naming a mechanism is reliable; a model
guessing the dimensions of an object it cannot see is not.
"""

from __future__ import annotations

from typing import Any

from .anchors import ANCHOR_NAMES, AXIS_NAMES

#: A hinge is a door, a slide is a drawer, a button is a slide with a spring, and fixed is
#: welded to the parent. ``button`` is sugar: the generator expands it into a sprung slide.
VALID_JOINTS = {"hinge", "slide", "button", "fixed"}

#: How the shell itself relates to the world. ``free`` gets a freejoint and falls; ``fixed``
#: is welded and is scenery with collision.
VALID_MOBILITY = {"free", "fixed"}

#: The shell's own geometry. ``box`` is solid; ``shell`` is five walls around a cavity.
VALID_SHAPES = {"box", "shell"}

# Axes and anchors come from `anchors`, which owns the resolvers. One table, so the validator
# can never allow a name the resolver would then reject.
VALID_AXES = set(AXIS_NAMES)
VALID_ANCHORS = set(ANCHOR_NAMES)

VALID_ACTIONS = {"grasp", "push", "press", "pull"}

#: Comparison operators allowed in a ``requires`` clause.
VALID_OPS = {">=", "<=", ">", "<", "=="}

# Plausibility bounds. These are not physics; they are "the model has clearly hallucinated".
MAX_HINGE_DEGREES = 180.0
MAX_SLIDE_METRES = 1.0
MAX_OBJECT_METRES = 3.0

DEFAULT_MOBILITY = "fixed"
DEFAULT_SHAPE = "box"


def _fmt(allowed: set[str]) -> str:
    return ", ".join(sorted(allowed))


def validate(schema: Any) -> list[str]:
    """Return a list of human-and-model readable problems. Empty means the schema is usable.

    Never raises on a malformed schema — a missing key is itself a finding, because the whole
    point is to hand the list back to the model rather than to crash on its output.
    """
    errs: list[str] = []

    if not isinstance(schema, dict):
        return [f"schema must be a JSON object, got {type(schema).__name__}"]

    if not schema.get("object"):
        errs.append("missing 'object': a noun naming what this is")

    mobility = schema.get("mobility", DEFAULT_MOBILITY)
    if mobility not in VALID_MOBILITY:
        errs.append(f"bad mobility '{mobility}'. allowed: {_fmt(VALID_MOBILITY)}")

    shape = schema.get("shape", DEFAULT_SHAPE)
    if shape not in VALID_SHAPES:
        errs.append(f"bad shape '{shape}'. allowed: {_fmt(VALID_SHAPES)}")

    errs += _validate_frame(schema.get("frame"))

    parts = schema.get("parts", [])
    if not isinstance(parts, list):
        errs.append(f"'parts' must be a list, got {type(parts).__name__}")
        return errs

    names: set[str] = set()
    for i, part in enumerate(parts):
        errs += _validate_part(part, i, names)

    # A part that must live inside a cavity needs a cavity to live in.
    if shape == "box":
        for part in parts:
            if isinstance(part, dict) and str(part.get("anchor", "")).startswith("interior_"):
                errs.append(
                    f"{part.get('name', '?')}: anchor '{part['anchor']}' is inside the shell, "
                    f"but shape is 'box' (solid). Use shape 'shell' for an object with a cavity."
                )

    # Preconditions are checked last, because they reference other parts by name and we need
    # the whole list — both to know the names exist and to know each one's joint kind.
    by_name = {p["name"]: p for p in parts if isinstance(p, dict) and p.get("name")}
    for part in parts:
        if not isinstance(part, dict):
            continue
        who = part.get("name", "?")
        for cond in part.get("requires", []) or []:
            errs += _validate_requires(cond, who, names, by_name)

    return errs


def _validate_frame(frame: Any) -> list[str]:
    if not isinstance(frame, dict):
        return ["missing 'frame': needs 'size' [x, y, z] in metres"]

    errs: list[str] = []
    size = frame.get("size")
    if not _is_vec3(size):
        errs.append(f"frame.size must be three numbers in metres, got {size!r}")
    else:
        if any(v <= 0 for v in size):
            errs.append(f"frame.size must be positive, got {size!r}")
        elif any(v > MAX_OBJECT_METRES for v in size):
            errs.append(
                f"frame.size {size!r} is over {MAX_OBJECT_METRES:g}m on an axis - "
                f"implausible for an object"
            )

    # origin is optional: rsrsplat places the object at the selection's centroid in scene
    # coordinates, so the schema does not need to carry a position.
    origin = frame.get("origin")
    if origin is not None and not _is_vec3(origin):
        errs.append(f"frame.origin must be three numbers in metres, got {origin!r}")
    return errs


def _is_vec3(value: Any) -> bool:
    return (
        isinstance(value, list)
        and len(value) == 3
        and all(isinstance(v, int | float) and not isinstance(v, bool) for v in value)
    )


def _validate_part(part: Any, index: int, names: set[str]) -> list[str]:
    if not isinstance(part, dict):
        return [f"parts[{index}] must be an object, got {type(part).__name__}"]

    errs: list[str] = []
    name = part.get("name")
    if not name:
        errs.append(f"parts[{index}] missing 'name'")
        name = f"parts[{index}]"
    elif name in names:
        errs.append(f"duplicate part name '{name}'")
    else:
        names.add(name)

    joint = part.get("joint")
    if joint not in VALID_JOINTS:
        errs.append(f"{name}: bad joint '{joint}'. allowed: {_fmt(VALID_JOINTS)}")

    # A fixed part is welded to its parent, so it has no axis and no range to check.
    if joint == "fixed":
        return errs + _validate_affordances(part, name)

    if part.get("axis") not in VALID_AXES:
        errs.append(f"{name}: bad axis '{part.get('axis')}'. allowed: {_fmt(VALID_AXES)}")

    if part.get("anchor") not in VALID_ANCHORS:
        errs.append(f"{name}: bad anchor '{part.get('anchor')}'. allowed: {_fmt(VALID_ANCHORS)}")

    errs += _validate_range(part, name, joint)
    errs += _validate_affordances(part, name)
    return errs


def _validate_range(part: dict, name: str, joint: Any) -> list[str]:
    rng = part.get("range")
    if not (isinstance(rng, list) and len(rng) == 2 and all(isinstance(v, int | float) for v in rng)):
        return [f"{name}: 'range' must be two numbers [low, high], got {rng!r}"]

    lo, hi = rng
    if lo > hi:
        return [f"{name}: range {rng!r} is inverted - low must not exceed high"]

    if joint == "hinge" and max(abs(lo), abs(hi)) > MAX_HINGE_DEGREES:
        return [
            f"{name}: hinge range {rng!r} exceeds {MAX_HINGE_DEGREES:g} degrees "
            f"(hinge ranges are in DEGREES)"
        ]

    if joint in ("slide", "button") and abs(hi - lo) > MAX_SLIDE_METRES:
        return [
            f"{name}: {joint} travel of {abs(hi - lo):g}m exceeds {MAX_SLIDE_METRES:g}m "
            f"({joint} ranges are in METRES)"
        ]

    return []


def _validate_affordances(part: dict, name: str) -> list[str]:
    errs: list[str] = []
    affordances = part.get("affordances", []) or []
    if not isinstance(affordances, list):
        return [f"{name}: 'affordances' must be a list"]
    for aff in affordances:
        if not isinstance(aff, dict):
            errs.append(f"{name}: each affordance must be an object with 'action' and 'site'")
            continue
        if aff.get("action") not in VALID_ACTIONS:
            errs.append(
                f"{name}: bad affordance action '{aff.get('action')}'. "
                f"allowed: {_fmt(VALID_ACTIONS)}"
            )
        if not aff.get("site"):
            errs.append(f"{name}: affordance '{aff.get('action')}' missing a 'site' name")
    return errs


def rest_position(part: dict) -> float:
    """Where a part sits when nothing is touching it: the end of its range nearer to zero."""
    lo, hi = part["range"]
    return lo if abs(lo) <= abs(hi) else hi


def _validate_requires(
    cond: Any, who: str, names: set[str], by_name: dict[str, dict]
) -> list[str]:
    """A precondition is the string ``'<part> <op> <value>'``, e.g. ``'door >= 80'``."""
    if not isinstance(cond, str):
        return [f"{who}: each 'requires' entry must be a string like 'door >= 80'"]

    fields = cond.split()
    if len(fields) != 3:
        return [f"{who}: requires '{cond}' is malformed - expected '<part> <op> <value>'"]

    target, op, value = fields
    errs: list[str] = []
    if target not in names:
        errs.append(f"{who}: requires unknown part '{target}'. known: {_fmt(names)}")
    if op not in VALID_OPS:
        errs.append(f"{who}: requires bad operator '{op}'. allowed: {_fmt(VALID_OPS)}")
    try:
        threshold = float(value)
    except ValueError:
        return errs + [f"{who}: requires '{cond}' - '{value}' is not a number"]

    if errs:
        return errs
    return _check_satisfiable(cond, who, target, op, threshold, by_name.get(target))


def _check_satisfiable(
    cond: str, who: str, target: str, op: str, threshold: float, part: dict | None
) -> list[str]:
    """Catch a precondition that can never be met, which validation used to let through.

    **A button is sprung.** It returns to rest the moment it is released, so a condition that
    is false at its rest position is false forever, and the part guarded by it can never move.
    That produces a twin which compiles, passes every other check, and is dead — and it is a
    *semantically reasonable* thing for a model to write, because a latch really does hold a
    lid shut. It is our vocabulary that cannot say so: there is no linkage primitive, only
    preconditions. Found live, on a briefcase described as ``lid requires latch <= -0.003``.

    The fix, if ever needed, is MuJoCo's ``<equality joint1 joint2 polycoef>`` as a fifth
    primitive. Until then this is a hard error with an explanation, so the retry can act on it.
    """
    if part is None or part.get("joint") != "button":
        return []
    try:
        rest = rest_position(part)
    except (KeyError, TypeError, ValueError):
        return []
    if OPS_SATISFIED[op](rest, threshold):
        return []
    return [
        f"{who}: requires '{cond}' can never be satisfied - '{target}' is a sprung button that "
        f"returns to {rest:g} whenever it is released, so it never reaches {threshold:g}. "
        f"A button cannot hold another part open; drop this precondition, or guard '{who}' with "
        f"a hinge or slide that stays where it is put."
    ]


#: Kept separate from ``precond.OPS`` so this module has no import cycle with it.
OPS_SATISFIED = {
    ">=": lambda a, b: a >= b,
    "<=": lambda a, b: a <= b,
    ">": lambda a, b: a > b,
    "<": lambda a, b: a < b,
    "==": lambda a, b: a == b,
}


# ---------------------------------------------------------------------------------------
# Accessors, so nothing downstream reaches into raw dict keys and re-derives a default
# ---------------------------------------------------------------------------------------


def half_extents(schema: dict) -> tuple[float, float, float]:
    """The object's half-extents, which is what every anchor resolves against.

    MJCF box ``size`` is half-extents while the schema states full size, and conflating the
    two produces an object exactly twice the size it should be — which reads as a scale bug
    somewhere else entirely.
    """
    hx, hy, hz = (v / 2 for v in schema["frame"]["size"])
    return hx, hy, hz


def mobility(schema: dict) -> str:
    return schema.get("mobility", DEFAULT_MOBILITY)


def shape(schema: dict) -> str:
    return schema.get("shape", DEFAULT_SHAPE)


def parts(schema: dict) -> list[dict]:
    return schema.get("parts", []) or []


def with_measured_frame(
    schema: dict, half: tuple[float, float, float], origin: tuple[float, float, float] | None = None
) -> dict:
    """Return a copy of ``schema`` whose frame is the one the selection measured.

    Language supplies the mechanism; perception supplies the pose. The model describes what
    kind of thing this is and how it moves. The half-extents come from PCA over the splats
    the user dragged a box around, and are doubled back into a full size here.
    """
    out = dict(schema)
    frame = dict(out.get("frame") or {})
    frame["size"] = [2.0 * half[0], 2.0 * half[1], 2.0 * half[2]]
    if origin is not None:
        frame["origin"] = list(origin)
    out["frame"] = frame
    return out
