/**
 * Finding up, and the floor.
 *
 * The synthetic cases build a room at a known, deliberately oblique orientation — because
 * the failure this replaced was an axis-aligned histogram that could only ever answer x, y
 * or z, and a COLMAP frame is not a rotation of any standard convention.
 *
 * The capture-backed case is the real check and prints what it found, because "the room came
 * out on its side" is the kind of thing that is obvious in one line of numbers and invisible
 * in a pass/fail.
 */

import { existsSync, readFileSync } from "node:fs";

import { PackedSplats, PlyReader } from "@sparkjsdev/spark";
import * as THREE from "three";
import { describe, expect, it } from "vitest";

import { alignScene, estimateUp, findPlanes, floorIsDenserEnd, layerScore, ROOM_HEIGHT_M } from "./ground";
import type { SplatCloud } from "./splats";

const CAPTURE = process.env.RSRSPLAT_CAPTURE;
const haveCapture = Boolean(CAPTURE && existsSync(CAPTURE));

/**
 * A room, built in a frame where up is oblique to every axis.
 *
 * Floor, ceiling, two walls, a table and some clutter — enough layers along up for the score
 * to have something to find, and enough wall for a wrong answer to be available.
 */
function obliqueRoom(tilt = new THREE.Euler(0.4, 0.3, 0.6)) {
  const rotation = new THREE.Quaternion().setFromEuler(tilt);
  const points: THREE.Vector3[] = [];
  let state = 11;
  const rand = () => ((state = (state * 1664525 + 1013904223) % 4294967296), state / 4294967296);

  const slab = (z: number, n: number, spread = 4) => {
    for (let i = 0; i < n; i++) {
      points.push(new THREE.Vector3((rand() - 0.5) * spread, (rand() - 0.5) * spread, z));
    }
  };

  slab(0, 9000); // floor: the densest surface, as in a real capture
  slab(2.6, 3000); // ceiling: flat and featureless
  slab(0.75, 1500, 1.4); // a table
  slab(1.6, 900, 1.0); // a shelf

  for (let i = 0; i < 3000; i++) {
    // Two walls.
    points.push(new THREE.Vector3(2, (rand() - 0.5) * 4, rand() * 2.6));
    points.push(new THREE.Vector3((rand() - 0.5) * 4, 2, rand() * 2.6));
  }

  const centers = new Float32Array(points.length * 3);
  points.forEach((p, i) => {
    const r = p.clone().applyQuaternion(rotation);
    centers.set([r.x, r.y, r.z], i * 3);
  });

  const trueUp = new THREE.Vector3(0, 0, 1).applyQuaternion(rotation);
  return {
    cloud: {
      packed: new PackedSplats(),
      centers,
      opacities: new Float32Array(points.length).fill(0.9),
      count: points.length,
    } as SplatCloud,
    trueUp,
  };
}

function sampleOf(cloud: SplatCloud): Float64Array {
  const out = new Float64Array(cloud.count * 3);
  out.set(cloud.centers);
  return out;
}

describe("layer score", () => {
  it("scores a room's up direction above its wall normals", () => {
    // The measurement the whole thing turns on. Floors, ceilings, tables and shelves stack
    // along up; a room has one opposing pair of walls in any horizontal direction.
    const { cloud, trueUp } = obliqueRoom();
    const points = sampleOf(cloud);
    const wall = new THREE.Vector3(1, 0, 0)
      .projectOnPlane(trueUp)
      .normalize();

    expect(layerScore(points, trueUp)).toBeGreaterThan(layerScore(points, wall));
  });
});

describe("finding planes", () => {
  it("finds the room's surfaces", () => {
    const { cloud } = obliqueRoom();
    const planes = findPlanes(sampleOf(cloud));
    expect(planes.length).toBeGreaterThanOrEqual(3);
    expect(planes[0].inliers).toBeGreaterThan(500);
  });

  it("is deterministic, so the same capture aligns the same way twice", () => {
    // RANSAC on a clock gives a scene that is upright on one load and on its side the next.
    const { cloud } = obliqueRoom();
    const a = findPlanes(sampleOf(cloud));
    const b = findPlanes(sampleOf(cloud));
    expect(a.map((p) => p.inliers)).toEqual(b.map((p) => p.inliers));
  });
});

describe("estimating up", () => {
  it("recovers an up direction that is oblique to every axis", () => {
    // The case the previous implementation could not express at all.
    const { cloud, trueUp } = obliqueRoom();
    const points = sampleOf(cloud);
    const { up } = estimateUp(findPlanes(points), points);

    // Sign is settled separately by density, so compare the axis, not the direction.
    expect(Math.abs(up.dot(trueUp))).toBeGreaterThan(0.98);
    expect(Math.abs(trueUp.x)).toBeGreaterThan(0.1); // genuinely oblique
    expect(Math.abs(trueUp.y)).toBeGreaterThan(0.1);
  });
});

describe("which end is the floor", () => {
  it("picks the denser extreme", () => {
    // A floor collects objects and is closely observed; a ceiling is flat and featureless.
    const floorLow = Float64Array.from([
      ...Array.from({ length: 900 }, () => 0.001 * Math.random()),
      ...Array.from({ length: 100 }, () => 2.6 - 0.001 * Math.random()),
    ]);
    expect(floorIsDenserEnd(floorLow)).toBe(true);

    const floorHigh = Float64Array.from([...floorLow].map((v) => 2.6 - v));
    expect(floorIsDenserEnd(floorHigh)).toBe(false);
  });
});

describe("aligning a scene", () => {
  it("stands an obliquely-oriented room upright and scales it to metres", () => {
    const { cloud } = obliqueRoom();
    const alignment = alignScene(cloud);

    expect(alignment.up.z).toBeCloseTo(1, 6);

    // Height along z should now be a room's height.
    let lo = Infinity;
    let hi = -Infinity;
    for (let i = 0; i < cloud.count; i++) {
      const z = cloud.centers[i * 3 + 2];
      if (z < lo) lo = z;
      if (z > hi) hi = z;
    }
    expect(hi - lo).toBeCloseTo(ROOM_HEIGHT_M, 0);
    expect(alignment.groundHeight).toBeCloseTo(lo, 1);
  });

  it("puts the floor at the bottom, not the ceiling", () => {
    const { cloud } = obliqueRoom();
    alignScene(cloud);

    // The floor was the densest slab, so most splats should sit near the low end.
    let low = 0;
    for (let i = 0; i < cloud.count; i++) if (cloud.centers[i * 3 + 2] < 0.3) low += 1;
    let high = 0;
    for (let i = 0; i < cloud.count; i++) if (cloud.centers[i * 3 + 2] > 2.3) high += 1;
    expect(low).toBeGreaterThan(high);
  });
});

describe.skipIf(!haveCapture)("the real capture", () => {
  it("finds a plausible room", async () => {
    const buf = readFileSync(CAPTURE!);
    const reader = new PlyReader({
      fileBytes: new Uint8Array(buf.buffer, buf.byteOffset, buf.byteLength),
    });
    await reader.parseHeader();

    const count = reader.numSplats;
    const centers = new Float32Array(count * 3);
    const opacities = new Float32Array(count);
    reader.parseSplats((i, x, y, z, _sx, _sy, _sz, _qx, _qy, _qz, _qw, opacity) => {
      centers[i * 3] = x;
      centers[i * 3 + 1] = y;
      centers[i * 3 + 2] = z;
      opacities[i] = opacity;
    });

    const cloud: SplatCloud = { packed: new PackedSplats(), centers, opacities, count };
    const started = performance.now();
    const alignment = alignScene(cloud);
    const elapsed = performance.now() - started;

    const span = (axis: number) => {
      const v = new Float32Array(count);
      for (let i = 0; i < count; i++) v[i] = centers[i * 3 + axis];
      v.sort();
      return v[Math.floor(count * 0.99)] - v[Math.floor(count * 0.01)];
    };

    // eslint-disable-next-line no-console
    console.log(
      [
        ``,
        `  planes found   ${alignment.planes}`,
        `  layer score    ${alignment.layerScore} peaks along the chosen up`,
        `  scale applied  x${alignment.appliedScale.toFixed(4)}`,
        `  ground height  ${alignment.groundHeight.toFixed(3)} m`,
        `  room p1..p99   ${span(0).toFixed(2)} x ${span(1).toFixed(2)} wide, ${span(2).toFixed(2)} tall`,
        `  took           ${elapsed.toFixed(0)} ms`,
        ``,
      ].join("\n"),
    );

    // A room is WIDE in two directions and SHORT in the third. If up were wrong, the height
    // would be comparable to the widths -- which is exactly what the axis-aligned version
    // produced here: 11.5 x 10.96 x 11.41.
    expect(span(2)).toBeCloseTo(ROOM_HEIGHT_M, 0);
    expect(span(0)).toBeGreaterThan(span(2));
    expect(span(1)).toBeGreaterThan(span(2));
  }, 180_000);
});
