"""The seam between the browser and the physics service.

MIRRORED BY HAND with ``web/src/types/protocol.ts``. Change one, change the other, and
update ``contract/fixtures/``. Both sides have tests over those fixtures and both go red
if the pair drifts.

Three conventions, non-negotiable, repeated at every field that carries a unit:

* Positions are METRES, in scene coordinates.
* Quaternions are (w, x, y, z), MuJoCo convention. Three.js takes (x, y, z, w); that
  conversion happens in exactly one place, in ``web/src/net/``.
* Angles crossing this socket are DEGREES. MuJoCo ``qpos`` is radians internally; convert
  at this boundary and nowhere else.

Wire format is JSON with camelCase keys, matching the TypeScript side. Python-side field
names are snake_case and aliased. Serialise with ``model_dump(by_alias=True)``.
"""

from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field

# --------------------------------------------------------------------------------------
# Primitives
# --------------------------------------------------------------------------------------

#: (x, y, z) in METRES, scene coordinates.
Vec3 = tuple[float, float, float]

#: Quaternion (w, x, y, z), MuJoCo convention. NOT (x, y, z, w).
Quat = tuple[float, float, float, float]

#: 3x3 matrix, COLUMN-MAJOR: (c0x, c0y, c0z, c1x, c1y, c1z, c2x, c2y, c2z).
#: Always right-handed; determinant must be positive.
Mat3 = tuple[float, float, float, float, float, float, float, float, float]

JointType = Literal["hinge", "slide", "button", "free", "fixed"]

#: A symbolic region of a selection's oriented bounding box.
#:
#: The backend never sees Gaussians, so it can never name splat indices. It names a region
#: instead, and the client resolves that region geometrically against the same canonical
#: frame. Symbolic, never coordinates -- the same discipline the articulation schema uses
#: for anchors.
#:
#: Regions are defined in NORMALISED LOCAL COORDINATES (u, v, w), each in [-1, 1], along a
#: selection's canonical axes, where u = +front, v = +left, w = +up. See Selection.axes.
#:
#:     all           everything
#:     front         u > 0             back          u < 0
#:     left          v > 0             right         v < 0
#:     top           w > 0             bottom        w < 0
#:     front_upper   u > 0 and w > 0   front_lower   u > 0 and w < 0
#:     top_third     w > 1/3           bottom_third  w < -1/3
#:     left_third    v > 1/3           right_third   v < -1/3
SubsetRegion = Literal[
    "all",
    "left",
    "right",
    "front",
    "back",
    "top",
    "bottom",
    "front_upper",
    "front_lower",
    "top_third",
    "bottom_third",
    "left_third",
    "right_third",
]


class Wire(BaseModel):
    """Base for every wire structure.

    Unknown fields are an error rather than a shrug: a typo in a hand-mirrored contract
    should fail loudly on the first message, not silently drop a field.
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        populate_by_name=True,
    )


# --------------------------------------------------------------------------------------
# Shared structures
# --------------------------------------------------------------------------------------


class WorldFrame(Wire):
    """Where the ground is and which way is up.

    The browser owns the Gaussians, so only the browser can fit this. Without it the
    service has no floor, and nothing dropped into the scene can ever come to rest.
    """

    #: Unit vector, scene coordinates.
    up: Vec3
    #: Signed distance in METRES from the scene origin to the ground plane, along ``up``.
    ground_height: float = Field(alias="groundHeight")
    #: METRES per scene unit. 1.0 when the capture is already metric.
    scene_scale: float = Field(alias="sceneScale", gt=0.0)


class Obstacle(Wire):
    """A solid box in the scanned room: a worktop, a table, a wall.

    **A splat stops nothing.** Nothing in a Gaussian cloud collides with anything, so without
    these the only solid thing in a scene is the ground plane -- an object knocked off a
    counter falls through the counter, through the floor, and out of the world.

    Derived in the browser, which owns the Gaussians. Deliberately coarse: a room made of a
    dozen boxes stops the same things a millimetre-accurate one would.

    Axis-aligned in the aligned frame, so no orientation is carried.
    """

    id: str
    kind: Literal["surface", "wall"]
    #: Centre. METRES, scene coordinates.
    position: Vec3
    #: Half-extents, matching MJCF box ``size`` semantics.
    half_extents: Vec3 = Field(alias="halfExtents")


class Selection(Wire):
    """What the user dragged a box around.

    A few dozen bytes describing the shape of a splat subset. The indices themselves stay
    in the browser and never cross this socket.
    """

    id: str
    splat_count: int = Field(alias="splatCount", ge=1)

    #: Centroid of the selected Gaussians. METRES, scene coordinates.
    centroid: Vec3

    #: Principal axes from PCA. COLUMN-MAJOR, right-handed, determinant POSITIVE.
    #:
    #: CANONICALISED by the client before sending, so both sides agree on what
    #: "front_lower" means without further negotiation:
    #:
    #:     column 0 = +front, column 1 = +left, column 2 = +up
    #:
    #: where +up is the scene up vector, and +front is the horizontal principal axis
    #: pointing back toward the camera at the moment the selection was committed. +left is
    #: then determined: up x front, which is what makes the set right-handed.
    #:
    #: This is the same frame the articulation schema and MJCF generation use, ported from
    #: the prototype's anchors table (+x front, +z up). One frame across the whole system,
    #: or the splats a region names are not the splats the generated joint moves.
    #:
    #: PCA eigenvectors come back with arbitrary sign, and a left-handed set is a
    #: reflection rather than a rotation -- it mirrors everything downstream. The client
    #: checks the determinant and flips a column when it is negative.
    axes: Mat3

    #: Half-extents along axes columns 0, 1, 2 respectively. METRES.
    #: HALF, matching MJCF box ``size`` semantics, not full width.
    half_extents: Vec3 = Field(alias="halfExtents")


class PoseUpdate(Wire):
    """Where one body is, right now."""

    body_name: str = Field(alias="bodyName")
    #: METRES, scene coordinates.
    position: Vec3
    #: (w, x, y, z), MuJoCo convention.
    orientation: Quat


class PhysicsPart(Wire):
    """One rigid body within a physicalised object, and how it is allowed to move."""

    #: Matches ``PoseUpdate.body_name``.
    body_name: str = Field(alias="bodyName")
    joint_type: JointType = Field(alias="jointType")

    #: DEGREES for hinge; METRES for slide and button. Never radians on the wire.
    #: ``None`` for ``free`` and ``fixed``, which have no range.
    range: tuple[float, float] | None = None

    #: Which of the selection's splats belong to this part. ``"all"`` is the whole
    #: selection. Resolved client-side against the canonical frame in ``Selection``.
    splat_subset: SubsetRegion = Field(default="all", alias="splatSubset")

    #: Where this body starts. The client stores its splat group relative to this pose,
    #: then applies each incoming ``PoseUpdate`` on top of it. Sent explicitly rather than
    #: inferred from the first ``pose.batch``, because implicit ordering dependencies
    #: turn into drift bugs.
    initial_pose: PoseUpdate = Field(alias="initialPose")


class PhysicsObject(Wire):
    """A selection that has become real."""

    id: str
    #: Human-readable, derived from the prompt.
    label: str
    selection_id: str = Field(alias="selectionId")
    #: Shell first, then each articulated part.
    #:
    #: **Order is significant.** Regions overlap by design -- the shell claims ``all`` and a
    #: door claims ``front`` -- so a splat belongs to the LAST part whose region contains it.
    #: The shell is therefore the fallback for everything no part claimed, and the client can
    #: assign in one pass without special-casing the shell.
    parts: tuple[PhysicsPart, ...]
    #: KILOGRAMS, total across all parts.
    mass_kg: float = Field(alias="massKg", gt=0.0)
    #: Sliding friction coefficient, dimensionless.
    #: Note: MuJoCo takes the elementwise MAXIMUM across a contact pair, so the generator
    #: sets this on both geoms. Setting one does nothing.
    friction: float = Field(ge=0.0)


# --------------------------------------------------------------------------------------
# Frontend -> Backend
# --------------------------------------------------------------------------------------


class SceneLoad(Wire):
    """The browser has a splat open. The service sets up a session around it."""

    type: Literal["scene.load"] = "scene.load"
    splat_id: str = Field(alias="splatId")
    splat_count: int = Field(alias="splatCount", ge=1)
    #: Ground plane, up vector and scale, fitted client-side from the point cloud.
    world: WorldFrame
    #: The room's solid geometry, so physics has something to happen against. Empty is legal
    #: and means a bare ground plane -- a scene where a bottle rolls off a worktop and
    #: straight through it.
    #:
    #: Required, not defaulted: a client that forgets to send it gets a room where nothing
    #: is solid, which is exactly the failure this field exists to prevent. Send an empty
    #: list to mean it deliberately.
    obstacles: tuple[Obstacle, ...]


class SelectionCommit(Wire):
    """The user released a selection box. Geometry only -- no indices."""

    type: Literal["selection.commit"] = "selection.commit"
    selection: Selection


class ObjectPhysicalize(Wire):
    """Make this selection real, according to this sentence."""

    type: Literal["object.physicalize"] = "object.physicalize"
    selection_id: str = Field(alias="selectionId")
    #: Free text from the user, e.g. "a dishwasher, the door hinges at the bottom".
    prompt: str


class ObjectRemove(Wire):
    type: Literal["object.remove"] = "object.remove"
    object_id: str = Field(alias="objectId")


class SimControl(Wire):
    type: Literal["sim.control"] = "sim.control"
    action: Literal["play", "pause", "reset"]


class MeshAttach(Wire):
    """Attach generated visual geometry to an existing object. Phase 9."""

    type: Literal["mesh.attach"] = "mesh.attach"
    object_id: str = Field(alias="objectId")
    glb_url: str = Field(alias="glbUrl")


ClientMessage = Annotated[
    Union[
        SceneLoad,
        SelectionCommit,
        ObjectPhysicalize,
        ObjectRemove,
        SimControl,
        MeshAttach,
    ],
    Field(discriminator="type"),
]


# --------------------------------------------------------------------------------------
# Backend -> Frontend
# --------------------------------------------------------------------------------------


class SceneReady(Wire):
    """Handshake acknowledgement, so the client is not racing session setup."""

    type: Literal["scene.ready"] = "scene.ready"
    session_id: str = Field(alias="sessionId")


class ObjectCreated(Wire):
    type: Literal["object.created"] = "object.created"
    object: PhysicsObject


class ObjectFailed(Wire):
    type: Literal["object.failed"] = "object.failed"
    selection_id: str = Field(alias="selectionId")
    #: Readable enough to show a user. Not a stack trace.
    reason: str


class PoseBatch(Wire):
    """Every physicalised body's pose at one instant. Broadcast at roughly 30 Hz."""

    type: Literal["pose.batch"] = "pose.batch"
    #: Simulation time in SECONDS since the last reset.
    t: float
    poses: tuple[PoseUpdate, ...]


class SimStatus(Wire):
    type: Literal["sim.status"] = "sim.status"
    running: bool
    step_count: int = Field(alias="stepCount", ge=0)


ServerMessage = Annotated[
    Union[SceneReady, ObjectCreated, ObjectFailed, PoseBatch, SimStatus],
    Field(discriminator="type"),
]


__all__ = [
    "ClientMessage",
    "JointType",
    "Mat3",
    "MeshAttach",
    "Obstacle",
    "ObjectCreated",
    "ObjectFailed",
    "ObjectPhysicalize",
    "ObjectRemove",
    "PhysicsObject",
    "PhysicsPart",
    "PoseBatch",
    "PoseUpdate",
    "Quat",
    "SceneLoad",
    "SceneReady",
    "Selection",
    "SelectionCommit",
    "ServerMessage",
    "SimControl",
    "SimStatus",
    "SubsetRegion",
    "Vec3",
    "WorldFrame",
    "Wire",
]
