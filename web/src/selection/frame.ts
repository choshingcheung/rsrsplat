/**
 * Turning a set of selected splats into an object frame.
 *
 * This is the measurement half of "language supplies the mechanism, perception supplies the
 * pose". The user drags a box; this works out where the object is, which way it faces, and
 * how big it is — and those nine numbers are the entire description that crosses the socket.
 *
 * Two things here will silently ruin everything downstream if they are wrong, and both have
 * the property that the result still *looks* plausible:
 *
 * 1. **PCA eigenvectors come back with arbitrary sign.** A set with negative determinant is
 *    a reflection, not a rotation. Feed one to MuJoCo and the object is mirrored: a door
 *    hinges on the wrong edge, and nothing about the render says so.
 * 2. **The axes must be canonicalised to (front, left, up).** That is the frame the schema's
 *    anchors and the generated MJCF both use. If the columns arrive in PCA's own order —
 *    longest axis first — then "bottom_front_edge" resolves against whichever axis happened
 *    to be longest, and a wardrobe's door ends up on its side.
 */

import * as THREE from "three";

import type { Mat3, Selection, Vec3 } from "../types/protocol";

export interface MeasuredFrame {
  centroid: THREE.Vector3;
  /** Columns, already canonical: front, left, up. */
  front: THREE.Vector3;
  left: THREE.Vector3;
  up: THREE.Vector3;
  /** Half-extents along front, left, up respectively. */
  halfExtents: THREE.Vector3;
}

/**
 * Fit an oriented frame to the selected splats.
 *
 * The axes are the object's own: PCA in the ground plane gives two horizontal directions,
 * and scene up gives the third. `cameraPosition` then picks WHICH of the four horizontal
 * faces is the front — the one turned toward where the user was standing, because that is
 * the side they selected and therefore the side a door is on.
 *
 * That split is the point, and it matches the wire contract exactly: "+front is the
 * horizontal PRINCIPAL AXIS pointing back toward the camera". Front has to be a choice among
 * the object's axes rather than a direction of its own, or the half-extents stop describing
 * the object. Without the camera at all, two identical selections made from opposite sides
 * would disagree about which side the door is on.
 */
export function measureFrame(
  centers: Float32Array,
  indices: ArrayLike<number>,
  sceneUp: THREE.Vector3,
  cameraPosition: THREE.Vector3,
): MeasuredFrame {
  const n = indices.length;
  const centroid = new THREE.Vector3();
  if (n === 0) {
    return {
      centroid,
      front: new THREE.Vector3(1, 0, 0),
      left: new THREE.Vector3(0, 1, 0),
      up: sceneUp.clone().normalize(),
      halfExtents: new THREE.Vector3(),
    };
  }

  for (let k = 0; k < n; k++) {
    const i = indices[k] * 3;
    centroid.x += centers[i];
    centroid.y += centers[i + 1];
    centroid.z += centers[i + 2];
  }
  centroid.divideScalar(n);

  const up = sceneUp.clone().normalize();

  // The object's own horizontal axes, from PCA in the ground plane. These, and NOT the
  // camera direction, are what the box is measured along.
  //
  // Taking +front straight from the view direction seems reasonable and quietly ruins the
  // measurement: a 600 mm box seen at 45 degrees measures 850 mm across, because the extent
  // along a rotated axis is the projection of the box onto it. The half-extents then
  // describe the viewpoint rather than the object, and the physics body is bigger than the
  // thing it stands for — by a factor that changes every time the user orbits.
  const principal = horizontalPrincipalAxis(centers, indices, centroid, up);
  const perpendicular = new THREE.Vector3().crossVectors(up, principal);

  // The camera's only job is to say WHICH of the four faces is the front: the one turned
  // toward where the user was standing. That is a choice among four axes, not a direction of
  // its own, so it cannot skew the extents.
  const toCamera = cameraPosition.clone().sub(centroid).projectOnPlane(up);
  const front = principal.clone();
  if (toCamera.lengthSq() > 1e-9) {
    toCamera.normalize();
    let best = -Infinity;
    for (const candidate of [
      principal,
      principal.clone().negate(),
      perpendicular,
      perpendicular.clone().negate(),
    ]) {
      const facing = candidate.dot(toCamera);
      if (facing > best) {
        best = facing;
        front.copy(candidate);
      }
    }
  }
  front.normalize();

  // left = up x front completes a right-handed set by construction, so the determinant is
  // +1 without needing a check. Deriving the third axis is what makes a reflection
  // impossible, rather than something to detect after the fact.
  const left = new THREE.Vector3().crossVectors(up, front).normalize();

  const halfExtents = extentsIn(centers, indices, centroid, front, left, up);
  return { centroid, front, left, up, halfExtents };
}

/**
 * The dominant horizontal direction of the selected splats, by PCA in the ground plane.
 *
 * This is what the object is measured along, so that its half-extents are a property of the
 * object rather than of where the camera happened to be. The 2x2 eigenvector is solved in
 * closed form; a general eigensolver here would be three times the code for one number.
 */
function horizontalPrincipalAxis(
  centers: Float32Array,
  indices: ArrayLike<number>,
  centroid: THREE.Vector3,
  up: THREE.Vector3,
): THREE.Vector3 {
  // Any two directions perpendicular to up will do as a basis for the ground plane.
  const a = new THREE.Vector3(1, 0, 0).projectOnPlane(up);
  if (a.lengthSq() < 1e-9) a.set(0, 1, 0).projectOnPlane(up);
  a.normalize();
  const b = new THREE.Vector3().crossVectors(up, a);

  let saa = 0;
  let sab = 0;
  let sbb = 0;
  const v = new THREE.Vector3();
  for (let k = 0; k < indices.length; k++) {
    const i = indices[k] * 3;
    v.set(centers[i], centers[i + 1], centers[i + 2]).sub(centroid);
    const x = v.dot(a);
    const y = v.dot(b);
    saa += x * x;
    sab += x * y;
    sbb += y * y;
  }

  // Principal direction of a symmetric 2x2 covariance.
  const theta = 0.5 * Math.atan2(2 * sab, saa - sbb);
  return a.clone().multiplyScalar(Math.cos(theta)).addScaledVector(b, Math.sin(theta)).normalize();
}

/** Half-extents along three given orthonormal axes. */
function extentsIn(
  centers: Float32Array,
  indices: ArrayLike<number>,
  centroid: THREE.Vector3,
  front: THREE.Vector3,
  left: THREE.Vector3,
  up: THREE.Vector3,
): THREE.Vector3 {
  let maxF = 0;
  let maxL = 0;
  let maxU = 0;
  const v = new THREE.Vector3();
  for (let k = 0; k < indices.length; k++) {
    const i = indices[k] * 3;
    v.set(centers[i], centers[i + 1], centers[i + 2]).sub(centroid);
    maxF = Math.max(maxF, Math.abs(v.dot(front)));
    maxL = Math.max(maxL, Math.abs(v.dot(left)));
    maxU = Math.max(maxU, Math.abs(v.dot(up)));
  }
  // Never zero: a flat selection with no depth would give MuJoCo a degenerate box, which it
  // accepts and then behaves strangely around.
  const floor = 1e-3;
  return new THREE.Vector3(Math.max(maxF, floor), Math.max(maxL, floor), Math.max(maxU, floor));
}

/** Column-major (front, left, up), the layout the wire declares. */
export function axesOf(frame: MeasuredFrame): Mat3 {
  const { front, left, up } = frame;
  return [front.x, front.y, front.z, left.x, left.y, left.z, up.x, up.y, up.z];
}

/** Determinant of a column-major 3x3. Positive means a rotation; negative is a reflection. */
export function determinant(m: Mat3): number {
  return (
    m[0] * (m[4] * m[8] - m[5] * m[7]) -
    m[3] * (m[1] * m[8] - m[2] * m[7]) +
    m[6] * (m[1] * m[5] - m[2] * m[4])
  );
}

/** The wire message for a committed selection. */
export function toSelection(id: string, frame: MeasuredFrame, splatCount: number): Selection {
  const axes = axesOf(frame);
  if (determinant(axes) < 0) {
    // Unreachable by construction, since `left` is derived as up x front. Asserted anyway:
    // a reflection reaching the service mirrors the object, and nothing downstream can tell.
    throw new Error(`selection ${id} produced a left-handed frame; this is a bug in measureFrame`);
  }
  return {
    id,
    splatCount,
    centroid: [frame.centroid.x, frame.centroid.y, frame.centroid.z] as Vec3,
    axes,
    halfExtents: [frame.halfExtents.x, frame.halfExtents.y, frame.halfExtents.z] as Vec3,
  };
}
