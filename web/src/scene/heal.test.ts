/**
 * Hole healing, on floors whose answer is known.
 *
 * Every test here asks the same question in a different way: would a person notice? So they
 * check where the patch sits, what colour it is and what it declines to invent — not counts.
 */

import * as THREE from "three";
import { PackedSplats } from "@sparkjsdev/spark";
import { describe, expect, it } from "vitest";

import { healSurface } from "./heal";
import type { SplatCloud } from "./splats";

interface Built {
  cloud: SplatCloud;
  removed: number[];
}

/**
 * A floor at z = 0 with a square object sitting on it, and NO floor underneath the object --
 * which is the whole point: a scanner never sees through a vest.
 */
function floorWithObject({
  half = 0.6,
  holeHalf = 0.15,
  step = 0.025,
  colourAt = () => new THREE.Color(0.4, 0.35, 0.3),
}: {
  half?: number;
  holeHalf?: number;
  step?: number;
  colourAt?: (x: number, y: number) => THREE.Color;
} = {}): Built {
  const centers: number[] = [];
  const packed = new PackedSplats();
  const opacities: number[] = [];
  const removed: number[] = [];

  const s = new THREE.Vector3(0.02, 0.02, 0.002);
  const q = new THREE.Quaternion();

  const add = (x: number, y: number, z: number, colour: THREE.Color, isObject: boolean) => {
    const i = centers.length / 3;
    centers.push(x, y, z);
    opacities.push(0.95);
    packed.pushSplat(new THREE.Vector3(x, y, z), s, q, 0.95, colour);
    if (isObject) removed.push(i);
  };

  for (let x = -half; x <= half; x += step) {
    for (let y = -half; y <= half; y += step) {
      const inHole = Math.abs(x) <= holeHalf && Math.abs(y) <= holeHalf;
      if (!inHole) add(x, y, 0, colourAt(x, y), false);
    }
  }
  // The object, sitting on top of the gap.
  for (let x = -holeHalf; x <= holeHalf; x += step) {
    for (let y = -holeHalf; y <= holeHalf; y += step) {
      for (let z = 0.01; z <= 0.05; z += step) {
        add(x, y, z, new THREE.Color(0.9, 0.1, 0.1), true);
      }
    }
  }

  return {
    cloud: {
      packed,
      centers: Float32Array.from(centers),
      opacities: Float32Array.from(opacities),
      count: opacities.length,
    },
    removed,
  };
}

/** Read a patch back out: positions and colours, in push order. */
function readPatch(patch: PackedSplats): { p: THREE.Vector3; c: THREE.Color }[] {
  const out: { p: THREE.Vector3; c: THREE.Color }[] = [];
  patch.forEachSplat((_i, c, _s, _q, _o, col) => {
    out.push({ p: c.clone(), c: col.clone() });
  });
  return out;
}

describe("healing the hole an object leaves", () => {
  it("fills the void, and puts the patch in the surface", () => {
    const { cloud, removed } = floorWithObject();
    const patch = healSurface(cloud, removed, 0);

    expect(patch.healed).toBe(true);
    const splats = readPatch(patch.splats);
    expect(splats.length).toBeGreaterThan(50);
    // Every patch splat lies in the floor, not floating where the object was.
    for (const { p } of splats) expect(Math.abs(p.z)).toBeLessThan(1e-6);
  });

  it("covers the hole and does not spill across the rest of the floor", () => {
    const { cloud, removed } = floorWithObject({ holeHalf: 0.15 });
    const splats = readPatch(healSurface(cloud, removed, 0).splats);

    // Inside the hole, allowing the cell the boundary falls in.
    for (const { p } of splats) {
      expect(Math.abs(p.x)).toBeLessThan(0.15 + 0.05);
      expect(Math.abs(p.y)).toBeLessThan(0.15 + 0.05);
    }
    // And it actually reaches the middle, rather than only the rim.
    expect(splats.some(({ p }) => Math.abs(p.x) < 0.03 && Math.abs(p.y) < 0.03)).toBe(true);
  });

  it("takes its colour from the NEAREST floor, not an average of all of it", () => {
    // A floor that is red on one side and blue on the other. Averaging gives purple across
    // the whole patch, which is exactly what reads as a patch. Nearest-neighbour keeps the
    // seam where the floor's own seam is.
    const { cloud, removed } = floorWithObject({
      colourAt: (x) => (x < 0 ? new THREE.Color(1, 0, 0) : new THREE.Color(0, 0, 1)),
    });
    const splats = readPatch(healSurface(cloud, removed, 0).splats);

    const left = splats.filter(({ p }) => p.x < -0.05);
    const right = splats.filter(({ p }) => p.x > 0.05);
    expect(left.length).toBeGreaterThan(0);
    expect(right.length).toBeGreaterThan(0);
    for (const { c } of left) expect(c.r).toBeGreaterThan(c.b);
    for (const { c } of right) expect(c.b).toBeGreaterThan(c.r);
  });

  it("invents nothing when there is no surface to copy", () => {
    // An object floating in space. There is no floor under it, so there is no honest patch,
    // and a plausible-looking one would be a lie in the middle of the frame.
    const packed = new PackedSplats();
    const centers: number[] = [];
    const opacities: number[] = [];
    const removed: number[] = [];
    const s = new THREE.Vector3(0.02, 0.02, 0.02);
    const q = new THREE.Quaternion();
    for (let i = 0; i < 300; i++) {
      const p = new THREE.Vector3((i % 10) * 0.02, Math.floor(i / 10) * 0.02, 1.5);
      centers.push(p.x, p.y, p.z);
      opacities.push(0.9);
      packed.pushSplat(p, s, q, 0.9, new THREE.Color(0.5, 0.5, 0.5));
      removed.push(i);
    }
    const cloud: SplatCloud = {
      packed,
      centers: Float32Array.from(centers),
      opacities: Float32Array.from(opacities),
      count: opacities.length,
    };

    const patch = healSurface(cloud, removed, 0);
    expect(patch.healed).toBe(false);
    expect(patch.count).toBe(0);
  });

  it("does not copy a table leg across the floor", () => {
    // Donors must lie IN the surface. A vertical structure beside the hole is not floor, and
    // smearing it sideways produces a streak that reads as a rendering fault.
    const { cloud, removed } = floorWithObject();
    const tall = healSurface(cloud, removed, 0, { band: 2.0 });
    const tight = healSurface(cloud, removed, 0, { band: 0.06 });
    // With a sane band the object's own red splats are excluded as donors.
    const reds = readPatch(tight.splats).filter(({ c }) => c.r > 0.7 && c.g < 0.3);
    expect(reds.length).toBe(0);
    expect(tall.healed).toBe(true); // the loose band still works, it is just less selective
  });

  it("returns nothing for an empty removal", () => {
    const { cloud } = floorWithObject();
    expect(healSurface(cloud, [], 0).healed).toBe(false);
  });
});
