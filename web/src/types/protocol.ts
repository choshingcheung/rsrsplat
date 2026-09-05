/**
 * The seam between the browser and the physics service.
 *
 * MIRRORED BY HAND with `service/app/protocol.py`. Change one, change the other, and
 * update `contract/fixtures/`. Both sides have tests over those fixtures and both go red
 * if the pair drifts.
 *
 * Three conventions, non-negotiable, repeated at every field that carries a unit:
 *
 * - Positions are METRES, in scene coordinates.
 * - Quaternions on the wire are (w, x, y, z), MuJoCo convention. Three.js
 *   `Quaternion.set` takes (x, y, z, w). That conversion happens in exactly one place,
 *   in `web/src/net/`, and nowhere else in this codebase.
 * - Angles crossing this socket are DEGREES. MuJoCo `qpos` is radians internally; the
 *   service converts at this boundary.
 */

// ---------------------------------------------------------------------------------------
// Primitives
// ---------------------------------------------------------------------------------------

/** (x, y, z) in METRES, scene coordinates. */
export type Vec3 = [number, number, number];

/**
 * Quaternion (w, x, y, z), MuJoCo convention. NOT (x, y, z, w).
 * Do not hand this to Three.js directly. See `web/src/net/`.
 */
export type Quat = [number, number, number, number];

/**
 * 3x3 matrix, COLUMN-MAJOR: [c0x, c0y, c0z, c1x, c1y, c1z, c2x, c2y, c2z].
 * Always right-handed; determinant must be positive.
 */
export type Mat3 = [
  number, number, number,
  number, number, number,
  number, number, number,
];

export type JointType = "hinge" | "slide" | "button" | "free" | "fixed";

/**
 * A symbolic region of a selection's oriented bounding box.
 *
 * The backend never sees Gaussians, so it can never name splat indices. It names a region
 * instead, and this client resolves that region geometrically against the same canonical
 * frame. Symbolic, never coordinates — the same discipline the articulation schema uses
 * for anchors.
 *
 * Regions are defined in NORMALISED LOCAL COORDINATES (u, v, w), each in [-1, 1], along a
 * selection's canonical axes, where u = +front, v = +left, w = +up. See `Selection.axes`.
 *
 *     all           everything
 *     front         u > 0             back          u < 0
 *     left          v > 0             right         v < 0
 *     top           w > 0             bottom        w < 0
 *     front_upper   u > 0 and w > 0   front_lower   u > 0 and w < 0
 *     top_third     w > 1/3           bottom_third  w < -1/3
 *     left_third    v > 1/3           right_third   v < -1/3
 */
export type SubsetRegion =
  | "all"
  | "left"
  | "right"
  | "front"
  | "back"
  | "top"
  | "bottom"
  | "front_upper"
  | "front_lower"
  | "top_third"
  | "bottom_third"
  | "left_third"
  | "right_third";

// ---------------------------------------------------------------------------------------
// Shared structures
// ---------------------------------------------------------------------------------------

/**
 * Where the ground is and which way is up.
 *
 * This browser owns the Gaussians, so only this browser can fit it. Without it the service
 * has no floor, and nothing dropped into the scene can ever come to rest.
 */
export interface WorldFrame {
  /** Unit vector, scene coordinates. */
  up: Vec3;
  /** Signed distance in METRES from the scene origin to the ground plane, along `up`. */
  groundHeight: number;
  /** METRES per scene unit. 1.0 when the capture is already metric. */
  sceneScale: number;
}

/**
 * A solid box in the scanned room: a worktop, a table, a wall.
 *
 * **A splat stops nothing.** Nothing in a Gaussian cloud collides with anything, so without
 * these the only solid thing in the scene is the ground plane — an object knocked off a
 * counter falls through the counter, through the floor, and out of the world.
 *
 * Derived in the browser, which owns the Gaussians: horizontal surfaces come from spikes in
 * the height histogram, walls from the floor's own footprint. Deliberately coarse. A room
 * made of a dozen boxes stops the same things a millimetre-accurate one would.
 *
 * Axis-aligned in the aligned frame, so no orientation is needed.
 */
export interface Obstacle {
  id: string;
  kind: "surface" | "wall";
  /** Centre. METRES, scene coordinates. */
  position: Vec3;
  /** Half-extents, matching MJCF box `size` semantics. */
  halfExtents: Vec3;
}

/**
 * One box of a selection's measured collision shape, in the selection's OWN frame.
 *
 * Axes are the selection's: `center` and `halfExtents` are along front, left and up, so the
 * service can drop them straight into the object's body without knowing anything about the
 * world.
 *
 * These come from voxelising the object's splats and merging the occupied cells. That is the
 * difference between a chair with legs and a cuboid containing the air between them — and
 * therefore between a chair that tips onto a corner and one that lands flat every time.
 */
export interface ShapeBox {
  center: Vec3;
  halfExtents: Vec3;
}

/**
 * What the user dragged a box around.
 *
 * A few dozen bytes describing the shape of a splat subset. The indices themselves stay
 * here in the browser and never cross the socket.
 */
export interface Selection {
  id: string;
  splatCount: number;

  /** Centroid of the selected Gaussians. METRES, scene coordinates. */
  centroid: Vec3;

  /**
   * Principal axes from PCA. COLUMN-MAJOR, right-handed, determinant POSITIVE.
   *
   * CANONICALISED here before sending, so both sides agree on what "front_lower" means
   * without further negotiation:
   *
   *     column 0 = +front, column 1 = +left, column 2 = +up
   *
   * where +up is the scene up vector, and +front is the horizontal principal axis pointing
   * back toward the camera at the moment the selection was committed. +left follows: it is
   * `up × front`, which is what makes the set right-handed.
   *
   * This is the same frame the articulation schema and MJCF generation use, ported from the
   * prototype's anchors table (+x front, +z up). One frame across the whole system, or the
   * splats a region names are not the splats the generated joint moves.
   *
   * PCA eigenvectors come back with arbitrary sign, and a left-handed set is a reflection
   * rather than a rotation — it mirrors everything downstream. Check the determinant and
   * flip a column when it is negative.
   */
  axes: Mat3;

  /**
   * Half-extents along axes columns 0, 1, 2 respectively. METRES.
   * HALF, matching MJCF box `size` semantics, not full width.
   */
  halfExtents: Vec3;

  /**
   * The object's measured shape, as boxes in this frame.
   *
   * Empty means "use the bounding box" — which is what the frame above already describes,
   * and is the honest fallback when segmentation could not find a clean component.
   */
  shape: ShapeBox[];
}

/** Where one body is, right now. */
export interface PoseUpdate {
  bodyName: string;
  /** METRES, scene coordinates. */
  position: Vec3;
  /** (w, x, y, z), MuJoCo convention. Convert before touching Three.js. */
  orientation: Quat;
}

/** One rigid body within a physicalised object, and how it is allowed to move. */
export interface PhysicsPart {
  /** Matches `PoseUpdate.bodyName`. */
  bodyName: string;
  jointType: JointType;

  /**
   * DEGREES for hinge; METRES for slide and button. Never radians on the wire.
   * `null` for `free` and `fixed`, which have no range.
   */
  range: [number, number] | null;

  /**
   * Which of the selection's splats belong to this part. `"all"` is the whole selection.
   * Resolved here, against the canonical frame in `Selection`.
   */
  splatSubset: SubsetRegion;

  /**
   * Where this body starts. Store this part's splat group relative to this pose, then
   * apply each incoming `PoseUpdate` on top of it. Sent explicitly rather than inferred
   * from the first `pose.batch`, because implicit ordering dependencies turn into drift
   * bugs.
   */
  initialPose: PoseUpdate;
}

/** A selection that has become real. */
export interface PhysicsObject {
  id: string;
  /** Human-readable, derived from the prompt. */
  label: string;
  selectionId: string;
  /**
   * Shell first, then each articulated part.
   *
   * **Order is significant.** Regions overlap by design — the shell claims `all` and a door
   * claims `front` — so a splat belongs to the LAST part whose region contains it. The shell
   * is therefore the fallback for everything no part claimed, and assignment is one pass with
   * no special case for the shell.
   */
  parts: PhysicsPart[];
  /** KILOGRAMS, total across all parts. */
  massKg: number;
  /** Sliding friction coefficient, dimensionless. */
  friction: number;
}

// ---------------------------------------------------------------------------------------
// Frontend -> Backend
// ---------------------------------------------------------------------------------------

/** The browser has a splat open. The service sets up a session around it. */
export interface SceneLoad {
  type: "scene.load";
  splatId: string;
  splatCount: number;
  /** Ground plane, up vector and scale, fitted here from the point cloud. */
  world: WorldFrame;
  /**
   * The room's solid geometry, so physics has something to happen against. Empty is legal
   * and means a bare ground plane — which is a scene where a bottle rolls off a worktop and
   * straight through it.
   */
  obstacles: Obstacle[];
}

/** The user released a selection box. Geometry only — no indices. */
export interface SelectionCommit {
  type: "selection.commit";
  selection: Selection;
}

/** Make this selection real, according to this sentence. */
export interface ObjectPhysicalize {
  type: "object.physicalize";
  selectionId: string;
  /** Free text from the user, e.g. "a dishwasher, the door hinges at the bottom". */
  prompt: string;
}

export interface ObjectRemove {
  type: "object.remove";
  objectId: string;
}

/**
 * Drive one joint to a position.
 *
 * Most of a room is fitted: a dishwasher is bolted in, and no amount of gravity opens its
 * door. Force is the wrong verb for those — the interaction is to WORK the mechanism, which
 * means driving the joint directly.
 *
 * `value` is in the same units as the part's `range`: DEGREES for a hinge, METRES for a
 * slide or button. Never radians.
 */
export interface JointSet {
  type: "joint.set";
  /** The part's `bodyName`, not its joint name. The service knows the mapping. */
  bodyName: string;
  value: number;
}

/**
 * Drag a free body toward a point, or let go of it.
 *
 * `target` null means release. The body is pulled by a damped spring rather than teleported,
 * so it collides with things on the way and keeps the momentum you gave it when you let go —
 * which is what makes throwing something feel like throwing something.
 *
 * Only meaningful for a `free` body. A fitted appliance ignores it; `joint.set` is how its
 * mechanism gets worked.
 */
export interface BodyDrag {
  type: "body.drag";
  bodyName: string;
  /** METRES, scene coordinates. Null releases. */
  target: Vec3 | null;
}

export interface SimControl {
  type: "sim.control";
  action: "play" | "pause" | "reset";
}

/** Attach generated visual geometry to an existing object. Phase 9. */
export interface MeshAttach {
  type: "mesh.attach";
  objectId: string;
  glbUrl: string;
}

export type ClientMessage =
  | SceneLoad
  | SelectionCommit
  | ObjectPhysicalize
  | ObjectRemove
  | JointSet
  | BodyDrag
  | SimControl
  | MeshAttach;

// ---------------------------------------------------------------------------------------
// Backend -> Frontend
// ---------------------------------------------------------------------------------------

/** Handshake acknowledgement, so the client is not racing session setup. */
export interface SceneReady {
  type: "scene.ready";
  sessionId: string;
}

export interface ObjectCreated {
  type: "object.created";
  object: PhysicsObject;
}

export interface ObjectFailed {
  type: "object.failed";
  selectionId: string;
  /** Readable enough to show a user. Not a stack trace. */
  reason: string;
}

/** Every physicalised body's pose at one instant. Broadcast at roughly 30 Hz. */
export interface PoseBatch {
  type: "pose.batch";
  /** Simulation time in SECONDS since the last reset. */
  t: number;
  poses: PoseUpdate[];
}

export interface SimStatus {
  type: "sim.status";
  running: boolean;
  stepCount: number;
}

export type ServerMessage =
  | SceneReady
  | ObjectCreated
  | ObjectFailed
  | PoseBatch
  | SimStatus;

// ---------------------------------------------------------------------------------------
// Drift guard
// ---------------------------------------------------------------------------------------

/**
 * Resolves to `T` only when every member of the union `U` appears in `T`; otherwise
 * resolves to an error tuple naming what is missing, which fails assignment loudly.
 *
 * Used to make the message-type lists below exhaustive at compile time, so a new message
 * type cannot be added on this side without also being listed — and therefore without a
 * fixture, which the Python side then demands too.
 */
type Exhaustive<T extends readonly string[], U extends string> = [
  Exclude<U, T[number]>,
] extends [never]
  ? T
  : ["missing message types:", Exclude<U, T[number]>];

export const CLIENT_MESSAGE_TYPES = [
  "scene.load",
  "selection.commit",
  "object.physicalize",
  "object.remove",
  "joint.set",
  "body.drag",
  "sim.control",
  "mesh.attach",
] as const satisfies Exhaustive<readonly ClientMessage["type"][], ClientMessage["type"]>;

export const SERVER_MESSAGE_TYPES = [
  "scene.ready",
  "object.created",
  "object.failed",
  "pose.batch",
  "sim.status",
] as const satisfies Exhaustive<readonly ServerMessage["type"][], ServerMessage["type"]>;

/**
 * `T` as JSON sees it: tuples relaxed to plain arrays, literal types widened to their base.
 *
 * `resolveJsonModule` infers `[0, 0, 1]` as `number[]` and `"scene.load"` as `string`, so a
 * fixture can never be assigned to `Vec3` or to a discriminated member directly. Widening
 * both leaves the compile-time check on FIELD NAMES and SCALAR KINDS — which is the drift
 * that actually happens when someone renames a field on one side of the seam.
 *
 * What this deliberately does not check, and where that is covered instead:
 *
 * - exact tuple lengths and literal values — pydantic, on the same fixtures
 * - discriminator tags — the runtime test in `protocol.fixtures.test.ts`
 * - extra unexpected fields — pydantic's `extra="forbid"` and its round-trip test
 *   (TypeScript's excess-property check does not apply to imported modules)
 */
export type Loose<T> = T extends string
  ? string
  : T extends number
    ? number
    : T extends boolean
      ? boolean
      : T extends readonly (infer U)[]
        ? Loose<U>[]
        : T extends object
          ? { [K in keyof T]: Loose<T[K]> }
          : T;

// ---------------------------------------------------------------------------------------
// Region resolution
// ---------------------------------------------------------------------------------------

/**
 * The half-space and slab tests behind each `SubsetRegion`, in normalised local
 * coordinates (u, v, w) ∈ [-1, 1]³ along a selection's canonical axes.
 *
 * Kept here beside the type it implements so the definition cannot drift away from the
 * documentation above. `web/src/selection/` applies these to actual splat centroids.
 */
export const REGION_TESTS: Record<
  SubsetRegion,
  (u: number, v: number, w: number) => boolean
> = {
  all: () => true,
  front: (u) => u > 0,
  back: (u) => u < 0,
  left: (_u, v) => v > 0,
  right: (_u, v) => v < 0,
  top: (_u, _v, w) => w > 0,
  bottom: (_u, _v, w) => w < 0,
  front_upper: (u, _v, w) => u > 0 && w > 0,
  front_lower: (u, _v, w) => u > 0 && w < 0,
  top_third: (_u, _v, w) => w > 1 / 3,
  bottom_third: (_u, _v, w) => w < -1 / 3,
  left_third: (_u, v) => v > 1 / 3,
  right_third: (_u, v) => v < -1 / 3,
};
