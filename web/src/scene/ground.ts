/**
 * Making a capture metric and z-up.
 *
 * A trained splat scene arrives in whatever frame the reconstruction happened to pick. There
 * is no up, no scale and no floor, and a physics engine needs all three. The browser does
 * this because it owns the Gaussians — see DECISIONS.md — and the service validates the
 * result rather than trusting it.
 *
 * **Up has to be derived, not guessed from a list of conventions.** The first version here
 * histogrammed along x, y and z and took the most layered of the three. On the playroom
 * capture that picks x, and the room comes out on its side — because a COLMAP frame is not a
 * rotation of any standard convention, so the true up is oblique to all three axes. The
 * prototype found the same thing and said so: *"none of identity, opencv_to_zup or
 * yup_to_zup produced a clean floor."*
 *
 * So, ported from `akitech/splat` `src/ground.py`:
 *
 * 1. **Sequential RANSAC** for the biggest planes — floors, ceilings, walls, tabletops.
 * 2. **Choose between their normals by layer score**: project every splat onto a candidate
 *    normal and count sharp peaks. Up produces many, because floors, ceilings, tabletops and
 *    shelves all stack along it; a wall normal produces few, because a room has only one
 *    opposing pair of walls in any direction. Measured on this capture at 16 layers along up
 *    against 4 along the wall normal.
 * 3. **Sign it by density.** A floor is the denser extreme: objects collect on it and a
 *    capture observes it closely, while a ceiling is flat and featureless. 34.7% of splats in
 *    the floor's band against 14.8% in the ceiling's.
 *
 * Two rules that sound right and both FAIL here, recorded so nobody reaches for them again:
 * the single largest plane is a **wall** (18.8% of splats), and so is the largest parallel
 * family, because one enormous wall outweighs four smaller horizontal ones.
 *
 * Gravity is genuinely not recoverable from a point cloud — real capture pipelines take it
 * from the device's IMU. Step 3 is therefore a proposal, and `alignScene(cloud, {flipUp})`
 * overrides it.
 */

import * as THREE from "three";

import type { Obstacle } from "../types/protocol";
import type { SplatCloud } from "./splats";

/** A domestic room, floor to ceiling. What the scale is fitted against. */
export const ROOM_HEIGHT_M = 2.6;

/** Splats below this percentile are strays, not the floor. */
const FLOOR_PERCENTILE = 0.5;

/** How many splats the plane fitting looks at. 1.5M would be minutes; this is under a second. */
const SAMPLE = 40_000;

export interface Plane {
  normal: THREE.Vector3;
  offset: number;
  inliers: number;
}

export interface Alignment {
  matrix: THREE.Matrix4;
  up: THREE.Vector3;
  /** Where the floor is, in the aligned frame. Rarely zero. */
  groundHeight: number;
  appliedScale: number;
  /** What the fit found, for a readout — and so a wrong answer is inspectable. */
  planes: number;
  layerScore: number;
}

/** A deterministic sample, so the same capture aligns the same way twice. */
function sample(centers: Float32Array, count: number, take = SAMPLE): Float64Array {
  const stride = Math.max(1, Math.floor(count / take));
  const n = Math.floor(count / stride);
  const out = new Float64Array(n * 3);
  for (let k = 0; k < n; k++) {
    const i = k * stride;
    out[k * 3] = centers[i * 3];
    out[k * 3 + 1] = centers[i * 3 + 1];
    out[k * 3 + 2] = centers[i * 3 + 2];
  }
  return out;
}

/** Mulberry32. Seeded so plane fitting is reproducible; RANSAC on a clock is not. */
function rng(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/**
 * Sequential RANSAC: fit the biggest plane, drop its inliers, repeat.
 *
 * `tol` is in the capture's own arbitrary units, so it is derived from the cloud's extent
 * rather than fixed — a tolerance in metres means nothing before the scale is known.
 */
export function findPlanes(points: Float64Array, count = 6, iterations = 300, seed = 1): Plane[] {
  const random = rng(seed);
  let remaining = points;
  const planes: Plane[] = [];

  // A half-percent of the cloud's own span. Thick enough to catch a real surface, thin
  // enough not to swallow the room.
  let lo = Infinity;
  let hi = -Infinity;
  for (let i = 0; i < points.length; i++) {
    if (points[i] < lo) lo = points[i];
    if (points[i] > hi) hi = points[i];
  }
  const tol = (hi - lo) * 0.005;

  for (let p = 0; p < count; p++) {
    const n = remaining.length / 3;
    if (n < 200) break;

    let bestNormal: [number, number, number] | null = null;
    let bestOffset = 0;
    let bestHits = 0;

    for (let it = 0; it < iterations; it++) {
      const ia = Math.floor(random() * n) * 3;
      const ib = Math.floor(random() * n) * 3;
      const ic = Math.floor(random() * n) * 3;

      const abx = remaining[ib] - remaining[ia];
      const aby = remaining[ib + 1] - remaining[ia + 1];
      const abz = remaining[ib + 2] - remaining[ia + 2];
      const acx = remaining[ic] - remaining[ia];
      const acy = remaining[ic + 1] - remaining[ia + 1];
      const acz = remaining[ic + 2] - remaining[ia + 2];

      let nx = aby * acz - abz * acy;
      let ny = abz * acx - abx * acz;
      let nz = abx * acy - aby * acx;
      const len = Math.hypot(nx, ny, nz);
      if (len < 1e-9) continue;
      nx /= len;
      ny /= len;
      nz /= len;
      const d = -(nx * remaining[ia] + ny * remaining[ia + 1] + nz * remaining[ia + 2]);

      let hits = 0;
      for (let k = 0; k < n; k++) {
        const j = k * 3;
        if (
          Math.abs(nx * remaining[j] + ny * remaining[j + 1] + nz * remaining[j + 2] + d) < tol
        ) {
          hits += 1;
        }
      }
      if (hits > bestHits) {
        bestHits = hits;
        bestNormal = [nx, ny, nz];
        bestOffset = d;
      }
    }

    if (!bestNormal || bestHits < 100) break;
    planes.push({
      normal: new THREE.Vector3(...bestNormal),
      offset: bestOffset,
      inliers: bestHits,
    });

    // Drop this plane's inliers and look for the next-biggest surface.
    const kept = new Float64Array((n - bestHits) * 3);
    let at = 0;
    for (let k = 0; k < n; k++) {
      const j = k * 3;
      const distance =
        bestNormal[0] * remaining[j] +
        bestNormal[1] * remaining[j + 1] +
        bestNormal[2] * remaining[j + 2] +
        bestOffset;
      if (Math.abs(distance) >= tol) {
        kept[at++] = remaining[j];
        kept[at++] = remaining[j + 1];
        kept[at++] = remaining[j + 2];
      }
    }
    remaining = kept.subarray(0, at);
  }

  return planes;
}

/**
 * How layered the cloud is along `normal`: the number of sharp peaks in its histogram.
 *
 * The measurement that picks up. A wall normal gives a broad, featureless spread; up gives a
 * comb of spikes, one per horizontal surface in the room.
 */
export function layerScore(points: Float64Array, normal: THREE.Vector3, bins = 200): number {
  const n = points.length / 3;
  let lo = Infinity;
  let hi = -Infinity;
  const projected = new Float64Array(n);

  for (let k = 0; k < n; k++) {
    const j = k * 3;
    const v = normal.x * points[j] + normal.y * points[j + 1] + normal.z * points[j + 2];
    projected[k] = v;
    if (v < lo) lo = v;
    if (v > hi) hi = v;
  }
  if (!(hi > lo)) return 0;

  const histogram = new Float64Array(bins);
  const width = (hi - lo) / bins;
  for (let k = 0; k < n; k++) {
    histogram[Math.min(bins - 1, Math.floor((projected[k] - lo) / width))] += 1;
  }

  const mean = n / bins;
  let peaks = 0;
  for (let b = 1; b < bins - 1; b++) {
    if (
      histogram[b] > mean * 2.5 &&
      histogram[b] > histogram[b - 1] &&
      histogram[b] >= histogram[b + 1]
    ) {
      peaks += 1;
    }
  }
  return peaks;
}

/** Which way is up, from how layered the scene is along each candidate plane normal. */
export function estimateUp(planes: Plane[], points: Float64Array): { up: THREE.Vector3; score: number } {
  if (!planes.length) return { up: new THREE.Vector3(0, 0, 1), score: 0 };

  // Near-parallel normals describe the same direction; keep one of each family.
  const candidates: THREE.Vector3[] = [];
  for (const plane of planes) {
    if (!candidates.some((c) => Math.abs(plane.normal.dot(c)) > 0.8)) {
      candidates.push(plane.normal.clone());
    }
  }

  let best = candidates[0];
  let bestScore = -1;
  for (const candidate of candidates) {
    const score = layerScore(points, candidate);
    if (score > bestScore) {
      bestScore = score;
      best = candidate;
    }
  }

  // RANSAC hands back a normal with an arbitrary sign, so the same scene would otherwise
  // come out upside down on a different seed. Canonicalise on the largest component and let
  // the density test below decide which end is really the floor.
  const up = best.clone().normalize();
  const largest = Math.abs(up.x) >= Math.abs(up.y) && Math.abs(up.x) >= Math.abs(up.z)
    ? up.x
    : Math.abs(up.y) >= Math.abs(up.z)
      ? up.y
      : up.z;
  if (largest < 0) up.negate();

  return { up, score: bestScore };
}

/**
 * True when the floor is at the LOW end — i.e. this way up is the right way up.
 *
 * A floor is the denser extreme. Objects collect on it and a capture observes it closely,
 * while a ceiling is flat and featureless.
 */
export function floorIsDenserEnd(values: Float64Array): boolean {
  const sorted = Float64Array.from(values).sort();
  const lo = sorted[Math.floor(sorted.length * 0.005)];
  const hi = sorted[Math.floor(sorted.length * 0.995)];
  const band = Math.max(hi - lo, 1e-9) * 0.023;

  let low = 0;
  let high = 0;
  for (const v of values) {
    if (v >= lo && v < lo + band) low += 1;
    if (v > hi - band && v <= hi) high += 1;
  }
  return low >= high;
}

/** Percentile of one axis of a packed xyz array. */
function percentile(centers: Float32Array, count: number, axis: 0 | 1 | 2, p: number): number {
  const values = new Float32Array(count);
  for (let i = 0; i < count; i++) values[i] = centers[i * 3 + axis];
  values.sort();
  return values[Math.min(count - 1, Math.max(0, Math.floor((count * p) / 100)))];
}

export interface AlignOptions {
  /** Override the density heuristic when it guesses the ceiling. One click, per the plan. */
  flipUp?: boolean;
}

/**
 * Work out the transform that makes this capture metric and z-up, and apply it in place.
 *
 * The centres are rewritten rather than left in the source frame, so selection, measurement
 * and the wire all speak the same coordinates. The same matrix goes onto the render mesh, or
 * the physics and the pixels disagree.
 */
export function alignScene(cloud: SplatCloud, options: AlignOptions = {}): Alignment {
  const { centers, count } = cloud;
  const points = sample(centers, count);

  const planes = findPlanes(points);
  const { up: found, score } = estimateUp(planes, points);
  const up = found.clone();

  // Which end is the floor.
  const n = points.length / 3;
  const along = new Float64Array(n);
  for (let k = 0; k < n; k++) {
    along[k] = up.x * points[k * 3] + up.y * points[k * 3 + 1] + up.z * points[k * 3 + 2];
  }
  if (!floorIsDenserEnd(along)) up.negate();
  if (options.flipUp) up.negate();

  // Rotate that direction onto +z, with no roll of its own.
  const rotation = new THREE.Quaternion().setFromUnitVectors(up, new THREE.Vector3(0, 0, 1));

  // Scale against the ROBUST height along up, never the bounding box: floaters inflate the
  // raw box threefold on this capture, and scaling by that makes the room three times too
  // small.
  const sortedAlong = Float64Array.from(along).sort();
  const lo = sortedAlong[Math.floor(n * 0.01)];
  const hi = sortedAlong[Math.floor(n * 0.99)];
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

  const groundHeight = percentile(centers, count, 2, FLOOR_PERCENTILE);

  return {
    matrix,
    up: new THREE.Vector3(0, 0, 1),
    groundHeight,
    appliedScale: scale,
    planes: planes.length,
    layerScore: score,
  };
}

// ---------------------------------------------------------------------------------------
// The room as solid geometry
// ---------------------------------------------------------------------------------------

/** A flat horizontal patch you could set something on: a floor, a table, a counter, a shelf. */
export interface Surface {
  height: number;
  centre: [number, number];
  halfExtent: [number, number];
  splats: number;
  /** Occupied area, not bounding-box area. */
  area: number;
  isFloor: boolean;
}

/**
 * Find the flat horizontal patches.
 *
 * After alignment a horizontal surface is a spike in the height histogram, so that is where
 * this looks. Each candidate band is then rasterised in xy, so the reported area is the
 * OCCUPIED footprint rather than a bounding box — a table and a shelf on opposite walls
 * share a height and must not merge into one enormous slab across the room.
 *
 * Two thresholds that are wrong in the obvious form, ported with the prototype's reasons:
 *
 * - A band must beat the maximum of its NEIGHBOURHOOD, but be compared for significance
 *   against the mean over EVERY bin including empty ones. Comparing against the local
 *   neighbourhood finds nothing, because a broad surface makes its own neighbourhood dense.
 * - The background is that mean, not the median of non-empty bins. The median fails exactly
 *   when a scene is mostly flat surfaces: with few occupied bins it sits between the peaks
 *   and rejects all of them.
 */
export function horizontalSurfaces(
  centers: Float32Array,
  count: number,
  { binHeight = 0.03, minSplats = 400, cell = 0.06, minArea = 0.05, slab = 0.04 } = {},
): Surface[] {
  const z = new Float32Array(count);
  for (let i = 0; i < count; i++) z[i] = centers[i * 3 + 2];
  const sorted = Float32Array.from(z).sort();

  // Padded by a bin at each end, or a scene that is a single flat band collapses to a
  // degenerate histogram and a surface in the first or last bin is missed entirely.
  const lo = sorted[0] - binHeight;
  const hi = sorted[Math.floor(count * 0.995)] + binHeight;
  const bins = Math.max(8, Math.floor((hi - lo) / binHeight));
  const width = (hi - lo) / bins;

  const counts = new Float64Array(bins);
  for (let i = 0; i < count; i++) {
    const b = Math.floor((z[i] - lo) / width);
    if (b >= 0 && b < bins) counts[b] += 1;
  }
  const background = counts.reduce((a, b) => a + b, 0) / bins;

  const surfaces: Surface[] = [];
  for (let i = 0; i < bins; i++) {
    if (counts[i] < minSplats) continue;
    let neighbourhood = 0;
    for (let k = Math.max(0, i - 4); k < Math.min(bins, i + 5); k++) {
      neighbourhood = Math.max(neighbourhood, counts[k]);
    }
    if (counts[i] < neighbourhood) continue;
    if (counts[i] < 3 * background) continue;

    const height = lo + (i + 0.5) * width;

    // Rasterise the band's footprint so the area is what is occupied, not what is spanned.
    const occupied = new Set<string>();
    let sumX = 0;
    let sumY = 0;
    let minX = Infinity;
    let maxX = -Infinity;
    let minY = Infinity;
    let maxY = -Infinity;
    let n = 0;

    for (let s = 0; s < count; s++) {
      if (Math.abs(centers[s * 3 + 2] - height) >= slab) continue;
      const x = centers[s * 3];
      const y = centers[s * 3 + 1];
      occupied.add(`${Math.floor(x / cell)},${Math.floor(y / cell)}`);
      sumX += x;
      sumY += y;
      if (x < minX) minX = x;
      if (x > maxX) maxX = x;
      if (y < minY) minY = y;
      if (y > maxY) maxY = y;
      n += 1;
    }
    if (n < minSplats) continue;

    const area = occupied.size * cell * cell;
    if (area < minArea) continue;

    surfaces.push({
      height,
      centre: [sumX / n, sumY / n],
      halfExtent: [(maxX - minX) / 2, (maxY - minY) / 2],
      splats: n,
      area,
      isFloor: false,
    });
  }

  surfaces.sort((a, b) => a.height - b.height);
  if (surfaces.length) surfaces[0].isFloor = true;
  return surfaces;
}

/**
 * The room, as boxes a physics engine can actually hit.
 *
 * **A splat stops nothing.** Without this the only collision geometry in the scene is the
 * ground plane, so an object knocked off a counter falls through the counter, through the
 * floor it was standing on, and out of the world. The walls, the tables and the worktops are
 * pure appearance until something like this makes them solid.
 *
 * Deliberately coarse, and the prototype's reason for that stands: the twin is a type
 * checker, and a room made of a dozen boxes rejects the same impossible situations a
 * millimetre-accurate one would.
 *
 * The extent comes from the FLOOR's own footprint rather than the whole cloud, because a
 * splat scene always has floaters and walls placed on the raw extent land metres past the
 * real ones — which is worse than having no walls at all.
 */
export function roomObstacles(
  centers: Float32Array,
  count: number,
  groundHeight: number,
  { wallThickness = 0.05, ceilingFallback = ROOM_HEIGHT_M } = {},
): Obstacle[] {
  const surfaces = horizontalSurfaces(centers, count, {});
  const floor = surfaces.find((s) => s.isFloor);
  const ceiling = surfaces.length > 1 ? surfaces[surfaces.length - 1].height : null;
  const height = ceiling && ceiling > groundHeight + 1 ? ceiling - groundHeight : ceilingFallback;

  let cx: number;
  let cy: number;
  let hx: number;
  let hy: number;
  if (floor) {
    [cx, cy] = floor.centre;
    [hx, hy] = floor.halfExtent;
  } else {
    const px = (axis: 0 | 1, p: number) => {
      const v = new Float32Array(count);
      for (let i = 0; i < count; i++) v[i] = centers[i * 3 + axis];
      v.sort();
      return v[Math.floor(count * p)];
    };
    cx = (px(0, 0.02) + px(0, 0.98)) / 2;
    cy = (px(1, 0.02) + px(1, 0.98)) / 2;
    hx = (px(0, 0.98) - px(0, 0.02)) / 2;
    hy = (px(1, 0.98) - px(1, 0.02)) / 2;
  }

  const obstacles: Obstacle[] = [];

  // Every surface that is not the floor and not the ceiling: worktops, tables, shelves.
  // Nothing rests on a ceiling, and the floor is already an infinite plane.
  surfaces.forEach((s, i) => {
    if (s.isFloor) return;
    if (ceiling !== null && s.height > ceiling - 0.15) return;
    obstacles.push({
      id: `surface_${i}`,
      kind: "surface",
      position: [s.centre[0], s.centre[1], s.height - 0.02],
      halfExtents: [Math.max(s.halfExtent[0], 0.05), Math.max(s.halfExtent[1], 0.05), 0.02],
    });
  });

  // Four walls at the floor's edge, so nothing slides out of the room.
  const walls: [string, number, number, number, number][] = [
    ["xlo", cx - hx, cy, wallThickness, hy],
    ["xhi", cx + hx, cy, wallThickness, hy],
    ["ylo", cx, cy - hy, hx, wallThickness],
    ["yhi", cx, cy + hy, hx, wallThickness],
  ];
  for (const [name, px, py, sx, sy] of walls) {
    obstacles.push({
      id: `wall_${name}`,
      kind: "wall",
      position: [px, py, groundHeight + height / 2],
      halfExtents: [Math.max(sx, wallThickness), Math.max(sy, wallThickness), height / 2],
    });
  }

  return obstacles;
}
