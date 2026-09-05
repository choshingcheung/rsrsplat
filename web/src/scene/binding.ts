/**
 * Making a scan's splats move with a physics body.
 *
 * This is the payoff. An object that becomes physical without visibly moving is not a demo,
 * it is a claim — so this is the one stage the plan says never to cut.
 *
 * The trick, and the reason the browser architecture is so much easier than the desktop one:
 *
 * **Bake each part's splats into the body's local frame once, at bind time.** Subtract the
 * body's initial position and rotate by the inverse of its initial orientation. After that
 * the mesh's own transform IS the body's pose, and Three.js composes it for free. No
 * per-Gaussian work per frame, no BVH refit, no shader.
 *
 * The desktop version had to write `R·local + t` into a world-space array every frame for
 * every splat, and then rebuild the acceleration structure or the splats vanished. Here it
 * is two property assignments per body.
 *
 * Two details that are easy to get wrong and hard to see afterwards:
 *
 * - **Bind against the body's INITIAL pose, not the identity.** A body that starts rotated —
 *   which every object in a real scan does, since selections are rarely axis-aligned — would
 *   otherwise have its splats snap to a new orientation on the first frame.
 * - **The splats left behind must be hidden.** The static scene still contains them, so
 *   without removing them the object appears twice: once falling, once as a ghost in the
 *   position it started from.
 */

import { PackedSplats, SplatMesh } from "@sparkjsdev/spark";
import * as THREE from "three";

import { toThree } from "../net/quaternion";
import type { MeasuredFrame } from "../selection/frame";
import { REGION_TESTS, type PhysicsObject, type PhysicsPart, type PoseUpdate } from "../types/protocol";
import type { SplatCloud } from "./splats";

/** One body's splats, in their own frame, ready to be given a pose. */
export interface BoundPart {
  bodyName: string;
  mesh: SplatMesh;
  splatCount: number;
}

export interface BoundObject {
  id: string;
  parts: BoundPart[];
  /**
   * The static scene with this object's splats removed — EXACTLY those splats, by index.
   *
   * The first version cut a box-shaped hole with an SDF instead. A selection's oriented box
   * contains the object plus the air around it, the floor under it and a slice of the wall
   * behind, so what it removed was a rectangular void rather than an object-shaped one. That
   * is the "not clean" removal: it was never removing the object, it was removing the
   * object's bounding box.
   */
  remaining: PackedSplats;
  /** Which splats left, so a later unbind can put them back. */
  removed: Uint32Array;
}

/**
 * Assign each of the selection's splats to exactly one part.
 *
 * Regions overlap by design — the shell claims `all`, a door claims `front` — so the rule
 * the contract states is that a splat belongs to the LAST part whose region contains it.
 * That makes the shell, listed first, the fallback for everything no part claimed, and lets
 * this run in one pass with no special case.
 */
export function assignParts(
  cloud: SplatCloud,
  indices: ArrayLike<number>,
  frame: MeasuredFrame,
  parts: readonly PhysicsPart[],
): Map<string, number[]> {
  const owned = new Map<string, number[]>();
  for (const part of parts) owned.set(part.bodyName, []);

  const d = new THREE.Vector3();
  const { centroid, front, left, up, halfExtents } = frame;

  for (let k = 0; k < indices.length; k++) {
    const i = indices[k];
    d.set(cloud.centers[i * 3], cloud.centers[i * 3 + 1], cloud.centers[i * 3 + 2]).sub(centroid);

    // Normalised local coordinates, which is the frame REGION_TESTS is defined in.
    const u = d.dot(front) / halfExtents.x;
    const v = d.dot(left) / halfExtents.y;
    const w = d.dot(up) / halfExtents.z;

    for (let p = parts.length - 1; p >= 0; p--) {
      if (REGION_TESTS[parts[p].splatSubset](u, v, w)) {
        owned.get(parts[p].bodyName)!.push(i);
        break;
      }
    }
  }
  return owned;
}

/** A body's initial pose, inverted once so every splat can be pushed into its frame. */
export interface BodyFrame {
  position: THREE.Vector3;
  rotation: THREE.Quaternion;
}

/**
 * The inverse of a body's initial pose.
 *
 * Captured against the body's pose AT BIND TIME, not the identity. Every object in a real
 * scan starts rotated, because a selection is rarely axis-aligned, and binding against
 * identity makes all of its splats snap to a new orientation on the very first frame.
 */
export function inverseOf(pose: PoseUpdate): BodyFrame {
  return {
    position: new THREE.Vector3(pose.position[0], pose.position[1], pose.position[2]),
    rotation: toThree(pose.orientation).invert(),
  };
}

/**
 * Express one splat in a body's frame: `local = R⁻¹ · (world − t)`.
 *
 * The splat's own orientation is composed with the same inverse. Rotating only the position
 * leaves every ellipsoid pointing the way it did in world space, so a door's splats smear
 * sideways as it swings — which reads as a rendering artefact rather than as a missing
 * multiplication.
 */
export function toBodyLocal(
  worldCentre: THREE.Vector3,
  worldRotation: THREE.Quaternion,
  body: BodyFrame,
  outCentre: THREE.Vector3,
  outRotation: THREE.Quaternion,
): void {
  outCentre.copy(worldCentre).sub(body.position).applyQuaternion(body.rotation);
  outRotation.copy(body.rotation).multiply(worldRotation);
}

/**
 * Build a movable splat mesh per part, and a static cloud with those splats removed.
 *
 * Both come out of ONE pass over the source. The pass has to happen anyway to gather each
 * part's splats, so taking the remainder at the same time costs nothing — and it is what
 * makes the removal exact rather than a box approximation.
 */
export function bind(
  cloud: SplatCloud,
  indices: ArrayLike<number>,
  frame: MeasuredFrame,
  object: PhysicsObject,
): BoundObject {
  const owned = assignParts(cloud, indices, frame, object.parts);
  const byName = new Map(object.parts.map((p) => [p.bodyName, p]));

  // One pass over the source cloud, marking which part each wanted splat belongs to.
  // Random access via getSplat would be a pass per splat.
  const partOf = new Int32Array(cloud.count).fill(-1);
  const order: string[] = [];
  for (const [bodyName, list] of owned) {
    const slot = order.length;
    order.push(bodyName);
    for (const i of list) partOf[i] = slot;
  }

  const packedFor = order.map(() => new PackedSplats());
  const counts = order.map(() => 0);

  // The inverse of each part's initial pose, to express its splats in the body's own frame.
  const inverse = order.map((bodyName) => inverseOf(byName.get(bodyName)!.initialPose));

  const remaining = new PackedSplats();

  const centre = new THREE.Vector3();
  const scales = new THREE.Vector3();
  const rotation = new THREE.Quaternion();
  const colour = new THREE.Color();

  cloud.packed.forEachSplat((i, c, s, q, opacity, col) => {
    const slot = partOf[i];
    if (slot < 0) {
      // Everything the object did not claim stays in the room, untouched.
      remaining.pushSplat(centre.copy(c), scales.copy(s), rotation.copy(q), opacity, colour.copy(col));
      return;
    }

    toBodyLocal(c, q, inverse[slot], centre, rotation);
    packedFor[slot].pushSplat(centre, scales.copy(s), rotation, opacity, colour.copy(col));
    counts[slot] += 1;
  });

  const parts: BoundPart[] = order.map((bodyName, slot) => {
    // raycastable, so a click can find which body it landed on. minRaycastOpacity keeps
    // the near-transparent haze around a scanned object from catching the ray before the
    // object itself does.
    const mesh = new SplatMesh({
      packedSplats: packedFor[slot],
      raycastable: true,
      minRaycastOpacity: 0.35,
    });
    const pose = byName.get(bodyName)!.initialPose;
    applyPose(mesh, pose);
    return { bodyName, mesh, splatCount: counts[slot] };
  });

  return {
    id: object.id,
    parts,
    remaining,
    removed: Uint32Array.from(indices),
  };
}

/** Take a bound object's meshes out of the scene. The static cloud is rebuilt by the caller. */
export function unbind(bound: BoundObject, scene: THREE.Object3D): void {
  for (const part of bound.parts) {
    scene.remove(part.mesh);
    part.mesh.dispose();
  }
}

/** Set a mesh's transform from a wire pose. The only per-frame work a bound part needs. */
export function applyPose(mesh: THREE.Object3D, pose: PoseUpdate): void {
  mesh.position.set(pose.position[0], pose.position[1], pose.position[2]);
  toThree(pose.orientation, mesh.quaternion);
}

/** The frame's rotation as a Three.js quaternion: columns front, left, up. */
export function orientationOf(frame: MeasuredFrame): THREE.Quaternion {
  const m = new THREE.Matrix4().makeBasis(frame.front, frame.left, frame.up);
  return new THREE.Quaternion().setFromRotationMatrix(m);
}
