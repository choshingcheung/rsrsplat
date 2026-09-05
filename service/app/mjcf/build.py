"""Articulation schema to MJCF.

Ported from ``akitech/splat`` ``src/gen_mjcf.py``. The invariants below came across intact;
the geometry hanging off them is new, because the prototype only ever built appliances and
rsrsplat has to build whatever the user selected.

**The four MuJoCo facts this file is built around.** Each one is silent when broken — the
model still compiles and still looks plausible in a viewer:

1. **A body with no joint is welded to its parent.** A ``fixed`` shell is therefore static
   scenery; a ``free`` one gets a ``<freejoint/>`` and falls. Forget it and "physicalise"
   produces something that never moves.
2. **Joint ``pos`` is the anchor in the child body's own frame.** So the body origin is put
   *on* the anchor and the joint anchored at ``0 0 0``, and the two can never disagree.
   Geometry is then offset from that origin rather than centred on the body.
3. **Box ``size`` is half-extents.** Passing a full width builds an object twice the size,
   which reads as a scale bug somewhere else entirely.
4. **Friction is the elementwise maximum across a contact pair.** Setting it on the object
   alone does nothing if the floor is slicker. ``scene.py`` writes the same value onto both.

Plus one from the compiler itself: without ``autolimits="true"`` every ``range`` is silently
ignored, and every joint spins freely through its own shell.

**What is new here.** The prototype hardcoded a door that hinges at the bottom and extends
upward. A lid hinges at the top and extends *down*; a side-hung door extends sideways. So a
panel's geometry is now derived from where its anchor sits on the front face — see
:func:`_panel_offset` — and a handle runs along the joint axis rather than always left-right.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import Any

from ..schema import (
    friction,
    half_extents,
    mass_kg,
    mobility,
    parts,
    resolve_anchor,
    resolve_axis,
    shape,
    validate,
)

#: Shell wall thickness and door panel thickness, metres. Thin enough not to eat the cavity,
#: thick enough that MuJoCo's contact solver does not tunnel through them at 0.002.
WALL = 0.008
PANEL = 0.008

#: Sprung-slide defaults for a button. Stiffness returns it; damping stops it ringing.
DEFAULT_SPRING = {"stiffness": 60, "damping": 0.4}

#: kg/m^3 for moving parts, so a door's mass follows its size instead of being hardcoded.
PART_DENSITY = 300.0

#: How far a button stands off the front face, metres, so it reads as pressable rather
#: than flush. Purely visual: intra-object contacts are excluded, so a button buried in a
#: door panel would no longer misbehave, only look wrong.
BUTTON_STANDOFF = PANEL + 0.012


class SchemaError(ValueError):
    """The schema did not validate. ``.errors`` is the list to feed back to the model."""

    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("schema failed validation:\n  " + "\n  ".join(errors))


def _v(*values: float) -> str:
    """Format for MJCF. Negative zero is collapsed, so reading a generated model is not a
    hunt for whether ``-0.00000`` means anything."""
    return " ".join(f"{0.0 if v == 0 else v:.5f}" for v in values)


# ---------------------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------------------


def _panel_offset(anchor: tuple[float, float, float], half: tuple[float, float, float]):
    """Where a hinged panel's centre sits, relative to its hinge.

    A hinge anchor is on an edge of the front face. The panel is the whole face, so its
    centre is displaced from the hinge by exactly the anchor's offset within that face,
    negated. That single rule covers every case the prototype hardcoded one of:

        bottom_front_edge  (hx, 0, -hz)   panel centre (P, 0, +hz)   extends up
        top_front_edge     (hx, 0, +hz)   panel centre (P, 0, -hz)   extends down
        left_front_edge    (hx, +hy, 0)   panel centre (P, -hy, 0)   extends right
        right_front_edge   (hx, -hy, 0)   panel centre (P, +hy, 0)   extends left

    Getting this wrong builds a lid that extends up out of the top of its own object.

    The ``P`` is one panel thickness forward, so the panel HANGS ON the front face rather
    than being embedded in it. Centred on the face instead, its inner half overlaps the side
    walls permanently: MuJoCo reports a standing 1.75 mm penetration and the solver spends
    every step pushing a door out of its own frame.
    """
    _, ay, az = anchor
    return (PANEL, -ay, -az)


def _opening_sign(
    axis: tuple[float, float, float], panel: tuple[float, float, float]
) -> float:
    """+1 or -1, chosen so that driving the joint POSITIVE swings the panel outward.

    Without this, ``range: [0, 90]`` means "opens" on a bottom-hinged door and "swings
    backwards into its own cavity" on a top-hinged lid, because the same axis sign produces
    opposite motions at opposite edges. Caught by the sweep test on the pedal bin, whose lid
    rotated down into the bin while looking, in every other respect, like a lid opening.

    The panel's instantaneous velocity under a positive rotation is ``axis x panel``, and
    front is +x, so the sign that makes that velocity point forward is the sign that opens
    it. The alternative -- leaving the model to pick the sign per anchor -- is asking it to
    reason about a cross product, when "opens 90 degrees" is what it can actually say.
    """
    forward = axis[1] * panel[2] - axis[2] * panel[1]  # (axis x panel).x
    return -1.0 if forward < -1e-12 else 1.0


def _handle_span(axis: tuple[float, float, float], half: tuple[float, float, float], scale=0.6):
    """A handle runs ALONG the hinge axis, so it is grabbed across the swing, not along it.

    Left-right for a lid, up-down for a side-hung door. The prototype always ran it
    left-right, which is right for a dishwasher and wrong for a fridge.
    """
    return tuple(a * h * scale for a, h in zip(axis, half, strict=True))


def _box_inertia(mass: float, half: tuple[float, float, float]) -> tuple[float, float, float]:
    """Diagonal inertia of a solid box, so an explicit mass gives exact dynamics.

    Stating ``<inertial>`` outright beats juggling per-geom densities to hit a target mass,
    and it is what makes a schema saying "45 kg" produce a body that weighs 45 kg.
    """
    hx, hy, hz = half
    k = mass / 3.0
    return (k * (hy * hy + hz * hz), k * (hx * hx + hz * hz), k * (hx * hx + hy * hy))


# ---------------------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------------------


def build_object(
    parent: ET.Element,
    schema: dict[str, Any],
    *,
    pos: tuple[float, float, float] = (0.0, 0.0, 0.0),
    quat: tuple[float, float, float, float] = (1.0, 0.0, 0.0, 0.0),
    prefix: str = "",
) -> ET.Element:
    """Append this schema's object as a body under ``parent``, and return it.

    ``pos`` and ``quat`` place it in scene coordinates -- in rsrsplat, at the centroid and
    orientation the user's selection measured. ``quat`` is (w, x, y, z), matching both
    MuJoCo and the wire protocol.

    ``prefix`` is prepended to every body, geom, joint and site name, so two crates in one
    scene do not collide in MuJoCo's flat namespace. The pose stream addresses bodies by
    these names, so whatever prefixes here must be what the session reports on the wire.

    Pass the returned body to :func:`exclude_internal_contacts` unless you specifically want
    an object's own parts colliding with one another.
    """
    half = half_extents(schema)
    name = prefix + str(schema["object"])
    mu = friction(schema)

    body = ET.SubElement(parent, "body", name=name, pos=_v(*pos), quat=_v(*quat))

    # Gotcha 1: no joint means welded to the parent. A free object needs to say so.
    if mobility(schema) == "free":
        ET.SubElement(body, "freejoint", name=f"{name}_free")

    # An explicit inertial, so a stated mass is the mass the solver uses.
    mass = mass_kg(schema)
    ET.SubElement(
        body,
        "inertial",
        pos="0 0 0",
        mass=f"{mass:.5f}",
        diaginertia=_v(*_box_inertia(mass, half)),
    )

    _add_shell_geoms(body, name, half, mu, shape(schema))

    for part in parts(schema):
        _add_part(body, part, half, mu, prefix)

    return body


def exclude_internal_contacts(mj: ET.Element, obj: ET.Element) -> None:
    """Stop one object's own parts colliding with each other.

    A door panel and a control button share the front face, so they overlap. A rack slides
    through the plane of a closed door. Left to the solver, these produce standing
    penetrations and forces that hold a sprung button permanently depressed -- which reads
    as a broken spring rather than as overlapping geometry.

    None of it should be resolved by contact anyway. **A part is constrained by its joint
    range, not by bumping into its neighbours**: ``autolimits`` is what stops a door at 90
    degrees, and the ordering question of "the rack cannot come out through a shut door" is
    a precondition, deliberately, because there is no linkage primitive in the vocabulary.

    Contacts with the floor and with everything outside the object are untouched.
    """
    names = [obj.get("name")] + [b.get("name") for b in obj.iter("body") if b is not obj]
    if len(names) < 2:
        return
    contact = ET.SubElement(mj, "contact")
    for i, a in enumerate(names):
        for b in names[i + 1 :]:
            ET.SubElement(contact, "exclude", body1=a, body2=b)


def _add_shell_geoms(
    body: ET.Element, name: str, half: tuple[float, float, float], mu: float, kind: str
) -> None:
    hx, hy, hz = half
    fric = _v(mu, 0.005, 0.0001)

    if kind == "box":
        # A crate. Gotcha 3: size is half-extents.
        ET.SubElement(
            body, "geom", name=f"{name}_body", type="box", size=_v(hx, hy, hz), friction=fric
        )
        return

    # A cavity with five walls and an open front, so a rack has somewhere to slide.
    walls = [
        ("back", (WALL, hy - WALL, hz), (-hx + WALL, 0.0, 0.0)),
        ("left", (hx, WALL, hz), (0.0, hy - WALL, 0.0)),
        ("right", (hx, WALL, hz), (0.0, -hy + WALL, 0.0)),
        ("top", (hx, hy - WALL, WALL), (0.0, 0.0, hz - WALL)),
        ("bottom", (hx, hy - WALL, WALL), (0.0, 0.0, -hz + WALL)),
    ]
    for wall, size, at in walls:
        ET.SubElement(
            body,
            "geom",
            name=f"{name}_{wall}",
            type="box",
            size=_v(*size),
            pos=_v(*at),
            friction=fric,
        )


def _add_part(
    shell: ET.Element,
    part: dict,
    half: tuple[float, float, float],
    mu: float,
    prefix: str = "",
) -> None:
    hx, hy, hz = half
    name = prefix + part["name"]
    joint = part["joint"]
    fric = _v(mu, 0.005, 0.0001)

    if joint == "fixed":
        # Welded to the shell: a trim piece or a fascia. No joint at all, by design.
        ET.SubElement(
            shell,
            "geom",
            name=f"{name}_panel",
            type="box",
            size=_v(PANEL, hy * 0.9, hz * 0.9),
            pos=_v(*resolve_anchor(part["anchor"], half)),
            friction=fric,
        )
        return

    anchor = resolve_anchor(part["anchor"], half)
    axis = resolve_axis(part["axis"])
    lo, hi = part["range"]

    # Gotcha 2: the body origin sits ON the anchor, so the joint anchors at 0 0 0.
    body = ET.SubElement(shell, "body", name=name, pos=_v(*anchor))

    if joint == "hinge":
        centre = _panel_offset(anchor, half)
        # Positive is the direction that opens it, whichever edge it hangs from.
        swing = tuple(_opening_sign(axis, centre) * a for a in axis)
        ET.SubElement(
            body,
            "joint",
            name=f"{name}_j",
            type="hinge",
            axis=_v(*swing),
            pos="0 0 0",
            range=f"{lo:g} {hi:g}",
            damping="0.8",
        )
        ET.SubElement(
            body,
            "geom",
            name=f"{name}_panel",
            type="box",
            size=_v(PANEL, hy, hz),
            pos=_v(*centre),
            rgba="0.85 0.85 0.88 1",
            density=str(PART_DENSITY),
            friction=fric,
        )
        # The handle sits at the panel's far edge from the hinge, and runs along the axis.
        far = tuple(2.0 * c for c in centre)
        span = _handle_span(axis, half)
        ET.SubElement(
            body,
            "geom",
            name=f"{name}_handle",
            type="capsule",
            size="0.010",
            fromto=_v(
                PANEL * 3.5,
                far[1] * 0.925 - span[1],
                far[2] * 0.925 - span[2],
                PANEL * 3.5,
                far[1] * 0.925 + span[1],
                far[2] * 0.925 + span[2],
            ),
            rgba="0.35 0.35 0.38 1",
            density=str(PART_DENSITY),
            friction=fric,
        )

    elif joint == "slide":
        ET.SubElement(
            body,
            "joint",
            name=f"{name}_j",
            type="slide",
            axis=_v(*axis),
            range=f"{lo:g} {hi:g}",
            damping="2.0",
        )
        ET.SubElement(
            body,
            "geom",
            name=f"{name}_tray",
            type="box",
            size=_v(hx * 0.88, hy * 0.87, 0.006),
            rgba="0.6 0.62 0.68 1",
            density=str(PART_DENSITY),
            friction=fric,
        )

    elif joint == "button":
        # Sugar for a sprung slide. springref returns it to rest; damping stops it ringing.
        spring = {**DEFAULT_SPRING, **(part.get("spring") or {})}
        # Stand it off the face so a door panel on the same face cannot lean on it.
        body.set("pos", _v(anchor[0] + BUTTON_STANDOFF, anchor[1], anchor[2]))
        ET.SubElement(
            body,
            "joint",
            name=f"{name}_j",
            type="slide",
            axis=_v(*axis),
            range=f"{lo:g} {hi:g}",
            stiffness=str(spring["stiffness"]),
            springref="0",
            damping=str(spring["damping"]),
        )
        ET.SubElement(
            body,
            "geom",
            name=f"{name}_cap",
            type="cylinder",
            size="0.012 0.006",
            euler="0 90 0",
            rgba="0.85 0.2 0.2 1",
            density=str(PART_DENSITY),
            friction=fric,
        )

    else:  # unreachable -- validate() rejects anything else
        raise ValueError(f"unknown joint type {joint!r}")

    for aff in part.get("affordances", []) or []:
        ET.SubElement(
            body,
            "site",
            name=prefix + aff["site"],
            size="0.008",
            pos=_v(*_site_offset(part, aff, anchor, half)),
            rgba="0 1 0 0.6",
        )


def _site_offset(
    part: dict, aff: dict, anchor: tuple[float, float, float], half: tuple[float, float, float]
) -> tuple[float, float, float]:
    """Where an affordance's site sits, in the part body's frame.

    Affordances name sites and the generator decides where they live, so geometry can be
    regenerated without invalidating anything that targets ``handle_grasp`` by name.
    """
    hx, _, _ = half
    action, joint = aff["action"], part["joint"]

    if joint == "hinge":
        centre = _panel_offset(anchor, half)
        far = tuple(2.0 * c for c in centre)
        if action in ("grasp", "pull"):
            return (PANEL * 3.5, far[1] * 0.925, far[2] * 0.925)  # on the handle
        return (PANEL * 1.25, centre[1] * 1.33, centre[2] * 1.33)  # flat on the panel
    if joint == "slide":
        return (hx * 0.85, 0.0, 0.008)  # the front lip of the tray
    if joint == "button":
        return (0.008, 0.0, 0.0)  # the exposed cap face
    return (0.0, 0.0, 0.0)


# ---------------------------------------------------------------------------------------
# A standalone model, for testing a schema on its own
# ---------------------------------------------------------------------------------------


def build(schema: dict[str, Any], *, floor: bool = True) -> ET.ElementTree:
    """One schema as a complete, loadable MJCF model.

    With ``floor=True`` the model gets a ground plane and the object stands on it, which is
    what you want in order to watch something settle. With ``floor=False`` there is no plane
    at all and the object sits at the origin, which is what a kinematic test wants: a joint
    driven through its range with nothing to collide against.

    Do not conflate the two. An object placed at the origin while a plane still exists at
    z = 0 starts half-buried in it and is ejected upward on the first step, which looks like
    a mass or a gravity bug and is neither.

    Raises ``SchemaError`` rather than generating from bad input. For placing an object into
    a real scanned scene, see ``scene.py`` -- this is the isolated case, used by tests and
    by anyone inspecting a single object in the MuJoCo viewer.
    """
    errors = validate(schema)
    if errors:
        raise SchemaError(errors)

    half = half_extents(schema)
    mu = friction(schema)

    mj = ET.Element("mujoco", model=str(schema["object"]))
    # degree, because a hinge range in radians is unreadable. autolimits, because without
    # it every range above is silently ignored.
    ET.SubElement(mj, "compiler", angle="degree", autolimits="true")
    ET.SubElement(mj, "option", timestep="0.002", gravity="0 0 -9.81")

    visual = ET.SubElement(mj, "visual")
    ET.SubElement(visual, "headlight", ambient="0.4 0.4 0.4", diffuse="0.6 0.6 0.6")

    default = ET.SubElement(mj, "default")
    ET.SubElement(default, "geom", rgba="0.75 0.75 0.78 1")
    ET.SubElement(default, "joint", damping="0.5")

    world = ET.SubElement(mj, "worldbody")
    ET.SubElement(world, "light", pos="0.4 0 1.2", dir="0 0 -1", directional="true")
    if floor:
        # Gotcha 4: the same friction goes on the floor, because MuJoCo takes the pair
        # maximum across a contact, and setting one side alone does nothing.
        ET.SubElement(
            world,
            "geom",
            name="floor",
            type="plane",
            size="4 4 0.1",
            rgba="0.25 0.25 0.28 1",
            friction=_v(mu, 0.005, 0.0001),
        )
        obj = build_object(world, schema, pos=(0.0, 0.0, half[2]))
    else:
        obj = build_object(world, schema)

    exclude_internal_contacts(mj, obj)
    return ET.ElementTree(mj)


def to_xml(schema: dict[str, Any], **kwargs: Any) -> str:
    """The MJCF for one schema, as an indented string."""
    tree = build(schema, **kwargs)
    ET.indent(tree, space="  ")
    return ET.tostring(tree.getroot(), encoding="unicode")


def compile_model(schema: dict[str, Any], **kwargs: Any):
    """Build and hand to MuJoCo, so "it generates" and "it loads" are never confused."""
    import mujoco

    return mujoco.MjModel.from_xml_string(to_xml(schema, **kwargs))


__all__ = [
    "BUTTON_STANDOFF",
    "PANEL",
    "PART_DENSITY",
    "WALL",
    "SchemaError",
    "build",
    "build_object",
    "exclude_internal_contacts",
    "compile_model",
    "to_xml",
]
