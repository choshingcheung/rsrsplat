/**
 * Making a capture metric and z-up.
 *
 * A trained splat scene arrives in whatever frame the reconstruction happened to pick. There
 * is no up, no scale and no floor, and a physics engine needs all three. The browser does
 * this because it owns the Gaussians — see DECISIONS.md — and the service validates the
 * result rather than trusting it.
 *
 * **This is the crude version, and it is honest about that.** The prototype's `ground.py`
 * derives up properly: RANSAC several planes, then choose between them by how LAYERED the
 * scene is along each candidate normal. A room stacks floors, ceilings, tabletops and
 * shelves along up, and stacks almost nothing along a wall normal — measured at 16 sharp
 * layers along up against 4 along the wall on the playroom capture. It also records two
 * plausible rules that both FAIL there: the single largest plane is a wall (18.8% of
 * splats), and so is the largest parallel family, because one enormous wall outweighs four
 * smaller horizontal ones.
 *
 * What is implemented here is the layer score, which is the part that actually decided it,
 * applied to three axis candidates rather than to RANSAC planes. That is enough for a
 * capture whose frame is a rotation of the usual conventions, and not enough for an
 * arbitrary COLMAP frame. Porting the plane fitting is the upgrade path.
 */

import * as THREE from "three";

import type { SplatCloud } from "./splats";

/** A domestic room, floor to ceiling. What the scale is fitted against. */
export const ROOM_HEIGHT_M = 2.6;

/** Splats below this percentile are treated as strays rather than as the floor. */
const FLOOR_PERCENTILE = 0.5;

export interface Alignment {
  /** Applied to the cloud's centres and to the render mesh, so both agree. */
  matrix: THREE.Matrix4;
  up: THREE.Vector3;
  /** Where the floor is, in the aligned frame. Rarely zero. */
  groundHeight: number;
  /** What the capture was scaled by to reach metres. Reported, not sent. */
  appliedScale: number;
}

/**
 * How layered the cloud is along `axis`: the number of sharp peaks in its histogram.
 *
 * The measurement that picks up. Floors, ceilings, tabletops and shelves all stack along
 * up, producing many spikes; a room has only one opposing pair of walls in any horizontal
 * direction, producing few.
 */
export function layerScore(centers: Float32Array, count: number, axis: 0 | 1 | 2, bins = 200): number {
  let lo = Infinity;
  let hi = -Infinity;
  for (let i = 0; i < count; i++) {
    const v = centers[i * 3 + axis];
    if (v < lo) lo = v;
    if (v > hi) hi = v;
  }
  if (!(hi > lo)) return 0;

  const histogram = new Float64Array(bins);
  const width = (hi - lo) / bins;
  for (let i = 0; i < count; i++) {
    const bin = Math.min(bins - 1, Math.floor((centers[i * 3 + axis] - lo) / width));
    histogram[bin] += 1;
  }

  const mean = count / bins;
  let peaks = 0;
  for (let b = 1; b < bins - 1; b++) {
    // A sharp peak: well above average, and above both neighbours. "Sharp" is the point —
    // a broad bulge is a wall seen edge-on, a spike is a surface.
    if (histogram[b] > mean * 2.5 && histogram[b] > histogram[b - 1] && histogram[b] >= histogram[b + 1]) {
      peaks += 1;
    }
  }
  return peaks;
}

/** Percentile of one axis. Sorting a copy, because the caller still needs the original. */
function percentile(centers: Float32Array, count: number, axis: 0 | 1 | 2, p: number): number {
  const values = new Float32Array(count);
  for (let i = 0; i < count; i++) values[i] = centers[i * 3 + axis];
  values.sort();
  return values[Math.min(count - 1, Math.max(0, Math.floor((count * p) / 100)))];
}

/**
 * Work out the transform that makes this capture metric and z-up, and apply it in place.
 *
 * The centres are rewritten rather than left in the source frame, so that selection,
 * measurement and the wire all speak the same coordinates. The same matrix goes onto the
 * render mesh, or the physics and the pixels disagree.
 */
export function alignScene(cloud: SplatCloud): Alignment {
  const { centers, count } = cloud;

  // 1. Which axis is up. Most captures are y-up or y-down; the layer score decides without
  //    having to know which convention this particular trainer used.
  const scores: [0 | 1 | 2, number][] = [
    [0, layerScore(centers, count, 0)],
    [1, layerScore(centers, count, 1)],
    [2, layerScore(centers, count, 2)],
  ];
  scores.sort((a, b) => b[1] - a[1]);
  const upAxis = scores[0][0];

  // 2. Which way along it. A room sits ABOVE its floor, so the half with more splats in it
  //    is the ceiling side. Comparing masses either side of the midpoint is enough.
  const lo = percentile(centers, count, upAxis, 1);
  const hi = percentile(centers, count, upAxis, 99);
  const mid = (lo + hi) / 2;
  let above = 0;
  for (let i = 0; i < count; i++) if (centers[i * 3 + upAxis] > mid) above += 1;
  const sign = above > count / 2 ? 1 : -1;

  // 3. Rotate that axis onto +z.
  const source = new THREE.Vector3();
  source.setComponent(upAxis, sign);
  const rotation = new THREE.Quaternion().setFromUnitVectors(source, new THREE.Vector3(0, 0, 1));

  // 4. Scale so the room is a room. Against the ROBUST extent, never the bounding box:
  //    floaters inflate the raw box threefold on the playroom capture, and scaling by that
  //    makes the room three times too small.
  const height = Math.abs(hi - lo);
  const scale = height > 1e-6 ? ROOM_HEIGHT_M / height : 1;

  const matrix = new THREE.Matrix4()
    .makeRotationFromQuaternion(rotation)
    .premultiply(new THREE.Matrix4().makeScale(scale, scale, scale));

  const v = new THREE.Vector3();
  for (let i = 0; i < count; i++) {
    v.set(centers[i * 3], centers[i * 3 + 1], centers[i * 3 + 2]).applyMatrix4(matrix);
    centers[i * 3] = v.x;
    centers[i * 3 + 1] = v.y;
    centers[i * 3 + 2] = v.z;
  }

  // 5. The floor, in the aligned frame. A percentile rather than the minimum, because a
  //    scene always has a few stray splats below the real floor and one of them would drag
  //    the whole room upward.
  const groundHeight = percentile(centers, count, 2, FLOOR_PERCENTILE);

  return { matrix, up: new THREE.Vector3(0, 0, 1), groundHeight, appliedScale: scale };
}
