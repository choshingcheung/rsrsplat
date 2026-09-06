/**
 * Segmenting from several angles at once, and voting.
 *
 * A mask is a silhouette, and a silhouette says nothing about depth. Carpet lying directly
 * under a vest projects INSIDE the vest's outline from every camera that looks down at it,
 * so a single view cannot reject it — only the depth band can, and a depth band is a blunt
 * instrument that either keeps the carpet or clips the object's own thickness.
 *
 * Seen from a low angle to one side, that same carpet lands well outside the outline. So the
 * object is what several viewpoints AGREE on. That converts a heuristic into a geometric
 * test, and it recovers the object's far side for free: the back of the vest is occluded
 * from the front but frontmost from behind, and a mask does not know about occlusion anyway.
 *
 * Two details that matter more than they look:
 *
 * - **The orbit is captured synchronously.** Every viewpoint is rendered and read back inside
 *   one task, before the browser paints, so the user never sees the camera swing away and
 *   back. `toDataURL` is synchronous where `toBlob` is not, which is the only reason this is
 *   possible at all.
 * - **The highlight is cleared first.** The selection is tinted with the accent colour, and
 *   photographing a lime-green vest and asking a model trained on photographs what it is
 *   invites a different answer than the one we want.
 */

import * as THREE from "three";

import type { View } from "./sam";
import type { ScreenRect } from "./pick";

export interface Viewpoint {
  camera: THREE.PerspectiveCamera;
  view: View;
  /** The selection's own bounds in this view, as the model's box prompt. */
  rect: ScreenRect;
}

/** Longest edge sent to the model, matching `sam.ts`. */
const MAX_EDGE = 1280;

/**
 * Render the scene from `count` viewpoints orbited around a target, and read each back.
 *
 * The first viewpoint is always the camera as it stands, so the user's own framing — the one
 * they chose because it shows the object well — is always in the vote.
 */
export function captureOrbit(
  renderer: THREE.WebGLRenderer,
  scene: THREE.Scene,
  camera: THREE.PerspectiveCamera,
  target: THREE.Vector3,
  centers: Float32Array,
  indices: ArrayLike<number>,
  count = 5,
  spreadDegrees = 55,
): Viewpoint[] {
  const width = renderer.domElement.width;
  const height = renderer.domElement.height;
  if (width === 0 || height === 0) return [];

  const original = camera.clone();
  const offset = new THREE.Vector3().subVectors(camera.position, target);
  const radius = offset.length();
  if (radius < 1e-6) return [];

  const up = new THREE.Vector3(0, 0, 1);
  const captured: { camera: THREE.PerspectiveCamera; dataUrl: string }[] = [];

  // Angles spread either side of the user's own view: 0, -s, +s, -2s, +2s, ...
  const angles: number[] = [0];
  const step = (spreadDegrees * Math.PI) / 180;
  for (let k = 1; angles.length < count; k++) {
    angles.push(-k * step, k * step);
  }
  angles.length = count;

  for (const angle of angles) {
    const rotated = offset.clone().applyAxisAngle(up, angle);
    camera.position.copy(target).add(rotated);
    camera.lookAt(target);
    camera.updateMatrixWorld(true);

    renderer.render(scene, camera);
    // Synchronous read-back. `toBlob` would yield to the event loop and the user would watch
    // the camera fly around the room.
    captured.push({ camera: camera.clone(), dataUrl: renderer.domElement.toDataURL("image/png") });
  }

  camera.position.copy(original.position);
  camera.quaternion.copy(original.quaternion);
  camera.updateMatrixWorld(true);
  renderer.render(scene, camera);

  const scale = Math.min(1, MAX_EDGE / Math.max(width, height));
  return captured.map(({ camera: view, dataUrl }) => ({
    camera: view,
    view: { blob: dataUrlToBlob(dataUrl), width, height, scale: 1 },
    rect: boundsIn(view, centers, indices),
  }));
}

/** The selection's screen bounds in one view, padded, as a prompt box. */
export function boundsIn(
  camera: THREE.PerspectiveCamera,
  centers: Float32Array,
  indices: ArrayLike<number>,
  pad = 0.04,
): ScreenRect {
  camera.updateMatrixWorld();
  const m = new THREE.Matrix4()
    .multiplyMatrices(camera.projectionMatrix, camera.matrixWorldInverse)
    .elements;

  let x0 = Infinity;
  let y0 = Infinity;
  let x1 = -Infinity;
  let y1 = -Infinity;

  for (let k = 0; k < indices.length; k++) {
    const j = indices[k] * 3;
    const x = centers[j];
    const y = centers[j + 1];
    const z = centers[j + 2];
    const w = m[3] * x + m[7] * y + m[11] * z + m[15];
    if (w <= 0) continue;
    const ndcX = (m[0] * x + m[4] * y + m[8] * z + m[12]) / w;
    const ndcY = (m[1] * x + m[5] * y + m[9] * z + m[13]) / w;
    if (ndcX < x0) x0 = ndcX;
    if (ndcX > x1) x1 = ndcX;
    if (ndcY < y0) y0 = ndcY;
    if (ndcY > y1) y1 = ndcY;
  }

  if (!Number.isFinite(x0)) return { x0: -1, y0: -1, x1: 1, y1: 1 };
  return {
    x0: Math.max(-1, x0 - pad),
    y0: Math.max(-1, y0 - pad),
    x1: Math.min(1, x1 + pad),
    y1: Math.min(1, y1 + pad),
  };
}

/**
 * Keep the splats that enough viewpoints agree on.
 *
 * Not unanimity. A single bad mask — the model latching onto a shadow, or the object leaving
 * frame at a steep angle — would otherwise empty the selection, and losing everything is a
 * worse failure than keeping a little carpet.
 */
export function vote(
  perView: ArrayLike<number>[],
  total: number,
  agreement = 0.6,
): Uint32Array {
  if (perView.length === 0) return new Uint32Array(0);

  const tally = new Uint8Array(total);
  for (const view of perView) {
    for (let k = 0; k < view.length; k++) tally[view[k]] += 1;
  }

  const needed = Math.max(1, Math.ceil(perView.length * agreement));
  const kept: number[] = [];
  for (let i = 0; i < total; i++) if (tally[i] >= needed) kept.push(i);
  return Uint32Array.from(kept);
}

/** A data URL back into bytes, without a network round trip. */
function dataUrlToBlob(dataUrl: string): Blob {
  const comma = dataUrl.indexOf(",");
  const binary = atob(dataUrl.slice(comma + 1));
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
  return new Blob([bytes], { type: "image/png" });
}
