/**
 * The renderer spike, kept as a test.
 *
 * A2 exists to retire one risk before any of the app is built: does the splat library give
 * per-splat centres AND let a subset become an independently transformable object? Without
 * both, selection and live binding are impossible, and finding that out in A7 would mean
 * rewriting the renderer with the demo already scheduled.
 *
 * The synthetic cases run everywhere. The real-capture cases run only when `RSRSPLAT_CAPTURE`
 * points at a PLY, because the file is 371 MB and lives outside the repo — and they skip
 * cleanly rather than failing, so a fresh clone is green.
 */

import { existsSync, readFileSync } from "node:fs";

import { PackedSplats } from "@sparkjsdev/spark";
import * as THREE from "three";
import { describe, expect, it } from "vitest";

import { bounds, centerOf, readPly, robustBounds, subset, type SplatCloud } from "./splats";

const CAPTURE = process.env.RSRSPLAT_CAPTURE;
const haveCapture = Boolean(CAPTURE && existsSync(CAPTURE));

/** Numbers from `python -m app.splat` on the same file. The reference implementation. */
const REFERENCE = {
  splats: 1_495_461,
  min: [-14.976, -24.465, -23.855],
  max: [12.537, 9.379, 10.401],
  opacity: [0.004, 1.0],
};

function synthetic(count = 2000, seed = 1): SplatCloud {
  // A deterministic box of splats, so a wrong answer is unambiguous rather than suspicious.
  const packed = new PackedSplats();
  const centers = new Float32Array(count * 3);
  const opacities = new Float32Array(count);
  const centre = new THREE.Vector3();
  const scales = new THREE.Vector3(0.01, 0.01, 0.01);
  const rotation = new THREE.Quaternion();
  const colour = new THREE.Color(0.5, 0.5, 0.5);

  let state = seed;
  const rand = () => {
    state = (state * 1664525 + 1013904223) % 4294967296;
    return state / 4294967296;
  };

  for (let i = 0; i < count; i++) {
    const x = (rand() - 0.5) * 2;
    const y = (rand() - 0.5) * 1;
    const z = (rand() - 0.5) * 0.5;
    centers[i * 3] = x;
    centers[i * 3 + 1] = y;
    centers[i * 3 + 2] = z;
    opacities[i] = 0.9;
    packed.pushSplat(centre.set(x, y, z), scales, rotation, 0.9, colour);
  }
  return { packed, centers, opacities, count };
}

describe("per-splat access", () => {
  it("exposes every centre as a flat array", () => {
    const cloud = synthetic(100);
    expect(cloud.centers.length).toBe(300);
    const v = centerOf(cloud, 7, new THREE.Vector3());
    expect(v.x).toBe(cloud.centers[21]);
    expect(v.z).toBe(cloud.centers[23]);
  });

  it("measures the bounds of what it read", () => {
    const { min, max } = bounds(synthetic(2000));
    expect(max.x - min.x).toBeGreaterThan(1.8);
    expect(max.z - min.z).toBeLessThan(0.6);
  });
});

describe("subset extraction", () => {
  it("takes exactly the splats it was asked for", () => {
    const cloud = synthetic(500);
    const indices = [0, 1, 2, 100, 250, 499];
    const out = subset(cloud, indices);
    expect(out.numSplats).toBe(indices.length);
  });

  it("preserves position to sub-millimetre at room scale", () => {
    // The packed format is quantised, so a round trip is close but not exact. Sub-millimetre
    // is irrelevant to physics and very relevant to knowing not to re-measure extents from it.
    const cloud = synthetic(500);
    const out = subset(cloud, [42]);
    const got = out.getSplat(0);
    const want = centerOf(cloud, 42, new THREE.Vector3());
    expect(got.center.distanceTo(want)).toBeLessThan(1e-3);
  });

  it("leaves the source cloud untouched", () => {
    const cloud = synthetic(500);
    const before = cloud.packed.numSplats;
    subset(cloud, [1, 2, 3]);
    expect(cloud.packed.numSplats).toBe(before);
  });

  it("handles an empty selection without producing a broken cloud", () => {
    expect(subset(synthetic(100), []).numSplats).toBe(0);
  });
});

describe("robust bounds", () => {
  it("ignores outliers that the raw bounding box does not", () => {
    const cloud = synthetic(1000);
    // One floater, of the kind a trained scene is full of.
    cloud.centers[0] = 500;
    const raw = bounds(cloud);
    const robust = robustBounds(cloud, 1);
    expect(raw.max.x).toBeGreaterThan(400);
    expect(robust.max.x).toBeLessThan(2);
  });
});

describe.skipIf(!haveCapture)("the real capture", () => {
  let cloud: SplatCloud;

  it("parses, and agrees with the Python reference exactly", async () => {
    const bytes = new Uint8Array(readFileSync(CAPTURE!));
    cloud = await readPly(bytes);

    expect(cloud.count).toBe(REFERENCE.splats);

    const { min, max } = bounds(cloud);
    for (const [i, axis] of (["x", "y", "z"] as const).entries()) {
      expect(min[axis]).toBeCloseTo(REFERENCE.min[i], 3);
      expect(max[axis]).toBeCloseTo(REFERENCE.max[i], 3);
    }
  }, 120_000);

  it("applies sigmoid to opacity, matching the reference range", () => {
    let lo = Infinity;
    let hi = -Infinity;
    for (const a of cloud.opacities) {
      if (a < lo) lo = a;
      if (a > hi) hi = a;
    }
    // Left pre-sigmoid these would run to the tens, in both directions.
    expect(lo).toBeCloseTo(REFERENCE.opacity[0], 3);
    expect(hi).toBeCloseTo(REFERENCE.opacity[1], 3);
  });

  it("finds the room is far smaller than its raw bounding box", () => {
    // Floaters inflate the raw box threefold. Anything that scales a capture by its longest
    // raw axis makes the room three times too small.
    const raw = bounds(cloud);
    const robust = robustBounds(cloud, 1);
    const rawLongest = Math.max(raw.max.x - raw.min.x, raw.max.y - raw.min.y, raw.max.z - raw.min.z);
    const robustLongest = Math.max(
      robust.max.x - robust.min.x,
      robust.max.y - robust.min.y,
      robust.max.z - robust.min.z,
    );
    expect(rawLongest).toBeGreaterThan(2.5 * robustLongest);
  });

  it("pulls a real object-sized subset out at interactive speed", () => {
    // Anchor on an actual splat: the middle of a room is air, and a box at the centroid of
    // a room scan comes back empty. That is correct, and it is confusing the first time.
    const anchor = centerOf(cloud, 700_000, new THREE.Vector3());
    const picked: number[] = [];
    const v = new THREE.Vector3();
    for (let i = 0; i < cloud.count; i++) {
      if (centerOf(cloud, i, v).distanceTo(anchor) < 0.25) picked.push(i);
    }

    expect(picked.length).toBeGreaterThan(100);
    const out = subset(cloud, picked);
    expect(out.numSplats).toBe(picked.length);
  }, 60_000);
});
