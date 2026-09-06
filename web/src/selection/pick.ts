/**
 * Box selection over a million Gaussians.
 *
 * Project every splat centre to screen space, keep the ones inside the rectangle, then throw
 * away everything behind the nearest surface. That last step is the whole difficulty: a
 * rectangle drawn over a dishwasher also contains the wall behind it, the floor under it and
 * whatever is in the next room, because a rectangle is a frustum and a frustum does not stop.
 *
 * Selecting the far wall along with the dishwasher does not look like a bug. It looks like a
 * dishwasher that weighs four hundred kilos and is three metres deep, and the first sign of
 * it is a physics body behaving oddly two stages later.
 *
 * Two filters, in order:
 *
 * 1. **Opacity.** A trained scene is full of near-transparent splats — on the playroom
 *    capture only 43% have alpha above 0.1. They contribute nothing visible, and counting
 *    them reports a number the user cannot reconcile with what is inside their box.
 * 2. **Depth.** Histogram the depths of what is left, find the nearest populated cluster,
 *    and keep only what is within a band of it. A room scan is layered along the view
 *    direction — object, gap, wall — and the near layer is what was pointed at.
 */

import * as THREE from "three";

import { masked, type Mask } from "./sam";

export interface ScreenRect {
  /** Normalised device coordinates, -1..1, y up. */
  x0: number;
  y0: number;
  x1: number;
  y1: number;
}

export interface PickOptions {
  /** Splats fainter than this are invisible, so selecting them is a lie about the count. */
  minOpacity?: number;
  /**
   * How deep a selection may reach behind its nearest surface, metres. Generous enough to
   * take a whole dishwasher, tight enough to leave the wall behind it.
   */
  depthBand?: number;
  /**
   * Take every nth splat. 1 is exact. During a drag a stride keeps the live count responsive
   * on a 1.5M-splat scene; the commit always runs at stride 1.
   */
  stride?: number;
  /**
   * An object silhouette from SAM, in device pixels.
   *
   * When present it REPLACES the rectangle: the rectangle was only ever a prompt, and the
   * mask is the answer. Depth filtering still applies, because the wall behind the vest is
   * inside the vest's silhouette too.
   */
  mask?: Mask | null;
}

const DEFAULTS = { minOpacity: 0.1, depthBand: 0.9, stride: 1 };

export interface PickResult {
  indices: Uint32Array;
  /** Depth of the nearest populated layer, for a readout during the drag. */
  nearDepth: number;
}

/** Rectangle in NDC from two pointer positions in client pixels. */
export function rectFromPointers(
  a: { x: number; y: number },
  b: { x: number; y: number },
  width: number,
  height: number,
): ScreenRect {
  const toNdcX = (v: number) => (v / width) * 2 - 1;
  const toNdcY = (v: number) => -((v / height) * 2 - 1);
  return {
    x0: Math.min(toNdcX(a.x), toNdcX(b.x)),
    x1: Math.max(toNdcX(a.x), toNdcX(b.x)),
    y0: Math.min(toNdcY(a.y), toNdcY(b.y)),
    y1: Math.max(toNdcY(a.y), toNdcY(b.y)),
  };
}

/**
 * Which splats are inside `rect`, in front of the far wall.
 *
 * Runs as one flat pass with no allocation per splat: at 1.5M centres, a `Vector3` per splat
 * is several hundred megabytes of garbage per drag frame.
 */
export function pick(
  centers: Float32Array,
  opacities: Float32Array,
  count: number,
  camera: THREE.Camera,
  rect: ScreenRect,
  options: PickOptions = {},
): PickResult {
  const { minOpacity, depthBand, stride } = { ...DEFAULTS, ...options };
  const mask = options.mask ?? null;

  camera.updateMatrixWorld();
  const viewProjection = new THREE.Matrix4().multiplyMatrices(
    camera.projectionMatrix,
    camera.matrixWorldInverse,
  );
  const m = viewProjection.elements;
  const eye = new THREE.Vector3().setFromMatrixPosition(camera.matrixWorld);

  // Pass one: inside the rectangle, visible. Keep candidates and their distance from the eye.
  const candidates: number[] = [];
  const depths: number[] = [];
  let minDepth = Infinity;
  let maxDepth = -Infinity;

  for (let i = 0; i < count; i += stride) {
    if (opacities[i] < minOpacity) continue;

    const j = i * 3;
    const x = centers[j];
    const y = centers[j + 1];
    const z = centers[j + 2];

    const w = m[3] * x + m[7] * y + m[11] * z + m[15];
    if (w <= 0) continue; // behind the camera

    const ndcX = (m[0] * x + m[4] * y + m[8] * z + m[12]) / w;
    const ndcY = (m[1] * x + m[5] * y + m[9] * z + m[13]) / w;

    if (mask) {
      // The mask REPLACES the rectangle rather than narrowing it. SAM's answer routinely
      // reaches outside the box it was prompted with, and that overspill is the part the
      // user's drag clipped -- the sleeve hanging out of the rectangle. Intersecting the
      // two would throw away the one thing a model can give us that a rectangle cannot.
      if (!masked(mask, ndcX, ndcY)) continue;
    } else {
      if (ndcX < rect.x0 || ndcX > rect.x1) continue;
      if (ndcY < rect.y0 || ndcY > rect.y1) continue;
    }

    const dx = x - eye.x;
    const dy = y - eye.y;
    const dz = z - eye.z;
    const depth = Math.sqrt(dx * dx + dy * dy + dz * dz);

    candidates.push(i);
    depths.push(depth);
    if (depth < minDepth) minDepth = depth;
    if (depth > maxDepth) maxDepth = depth;
  }

  if (!candidates.length) return { indices: new Uint32Array(0), nearDepth: 0 };

  const nearDepth = nearestLayer(depths, minDepth, maxDepth);
  const limit = nearDepth + depthBand;

  // Pass two: keep the near layer.
  const kept = new Uint32Array(candidates.length);
  let n = 0;
  for (let k = 0; k < candidates.length; k++) {
    if (depths[k] <= limit) kept[n++] = candidates[k];
  }

  return { indices: kept.subarray(0, n), nearDepth };
}

/**
 * The distance to the nearest populated depth layer.
 *
 * Not simply the minimum: a single stray splat floating in front of the object would drag
 * the whole band forward and take nothing with it. A histogram finds the first depth with
 * real mass behind it, which is the surface the user was actually pointing at.
 */
function nearestLayer(depths: number[], min: number, max: number, bins = 64): number {
  if (max - min < 1e-6) return min;

  const histogram = new Uint32Array(bins);
  const width = (max - min) / bins;
  for (const d of depths) {
    const bin = Math.min(bins - 1, Math.floor((d - min) / width));
    histogram[bin] += 1;
  }

  let peak = 0;
  for (const c of histogram) peak = Math.max(peak, c);

  // The first bin holding at least a tenth of the busiest one. A lower threshold picks up
  // reconstruction noise in front of the object; a higher one skips a thin near surface,
  // such as a cupboard door, in favour of the shelves behind it.
  const threshold = Math.max(1, peak * 0.1);
  for (let b = 0; b < bins; b++) {
    if (histogram[b] >= threshold) return min + b * width;
  }
  return min;
}
