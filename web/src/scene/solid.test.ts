/**
 * Static scene collision, on rooms whose answer is known.
 *
 * The question each test answers is "would a thrown object stop where it should", because
 * that is the only thing this geometry is for.
 */

import { describe, expect, it } from "vitest";

import { sceneCollision, SOLID_CELL } from "./solid";
import type { Obstacle } from "../types/protocol";

/** Points on a lattice over a box, as a scan of a solid surface would leave them. */
function slab(
  out: number[],
  centre: [number, number, number],
  size: [number, number, number],
  step = 0.02,
): void {
  for (let x = -size[0] / 2; x <= size[0] / 2; x += step) {
    for (let y = -size[1] / 2; y <= size[1] / 2; y += step) {
      for (let z = -size[2] / 2; z <= size[2] / 2; z += step) {
        out.push(centre[0] + x, centre[1] + y, centre[2] + z);
      }
    }
  }
}

function cloudOf(points: number[], opacity = 0.9) {
  const count = points.length / 3;
  return {
    centers: Float32Array.from(points),
    count,
    opacities: Float32Array.from({ length: count }, () => opacity),
  };
}

/** Is this world point inside any obstacle? What the solver effectively asks. */
function solidAt(boxes: Obstacle[], p: [number, number, number]): boolean {
  return boxes.some((b) =>
    Math.abs(p[0] - b.position[0]) <= b.halfExtents[0] &&
    Math.abs(p[1] - b.position[1]) <= b.halfExtents[1] &&
    Math.abs(p[2] - b.position[2]) <= b.halfExtents[2],
  );
}

describe("static scene collision", () => {
  it("makes a scanned worktop solid", () => {
    const points: number[] = [];
    slab(points, [0, 0, 0.01], [3, 3, 0.02]); // floor
    slab(points, [0.8, 0, 0.9], [1.2, 0.6, 0.04]); // a worktop at 90 cm
    const { centers, count, opacities } = cloudOf(points);

    const boxes = sceneCollision(centers, count, opacities, 0);

    expect(solidAt(boxes, [0.8, 0, 0.9])).toBe(true); // the worktop stops things
    expect(solidAt(boxes, [0.8, 0, 1.6])).toBe(false); // the air above it does not
  });

  it("leaves the gap under a table open, so things can be pushed beneath it", () => {
    // The reason this is voxels and not a bounding box. A bounding box round a table is a
    // solid cube and nothing goes under it.
    const points: number[] = [];
    slab(points, [0, 0, 0.01], [3, 3, 0.02]);
    slab(points, [0, 0, 0.74], [1.0, 0.8, 0.04]); // top
    for (const [x, y] of [[-0.45, -0.35], [0.45, -0.35], [-0.45, 0.35], [0.45, 0.35]]) {
      slab(points, [x, y, 0.36], [0.06, 0.06, 0.72]); // legs
    }
    const { centers, count, opacities } = cloudOf(points);

    const boxes = sceneCollision(centers, count, opacities, 0);

    expect(solidAt(boxes, [0, 0, 0.74])).toBe(true); // the top is there
    expect(solidAt(boxes, [0, 0, 0.4])).toBe(false); // the space under it is not
    expect(solidAt(boxes, [-0.45, -0.35, 0.4])).toBe(true); // but the leg is
  });

  it("ignores floaters, which every capture has", () => {
    // Stray splats below the floor and out in the void. A cell holding a handful of them is
    // haze, not surface, and a box there is an invisible obstacle in mid-air.
    const points: number[] = [];
    slab(points, [0, 0, 0.01], [2, 2, 0.02]);
    for (let i = 0; i < 40; i++) points.push(0.5 + i * 0.001, 0.5, 1.5); // a wisp
    const { centers, count, opacities } = cloudOf(points);

    const boxes = sceneCollision(centers, count, opacities, 0, { minSplatsPerCell: 60 });
    expect(solidAt(boxes, [0.5, 0.5, 1.5])).toBe(false);
  });

  it("drops splats too faint to be surface", () => {
    const points: number[] = [];
    slab(points, [0, 0, 0.5], [0.5, 0.5, 0.5]);
    const { centers, count } = cloudOf(points);
    const haze = Float32Array.from({ length: count }, () => 0.02);

    expect(sceneCollision(centers, count, haze, 0).length).toBe(0);
  });

  it("excludes a physicalised object, which must not also be scenery", () => {
    // Left as scenery, the object is born inside a frozen copy of itself and the solver
    // resolves that by launching it across the room.
    const points: number[] = [];
    slab(points, [0, 0, 0.01], [3, 3, 0.02]);
    const floorCount = points.length / 3;
    slab(points, [1.0, 1.0, 0.3], [0.4, 0.4, 0.6]); // the object
    const { centers, count, opacities } = cloudOf(points);

    const owned = Uint32Array.from(
      { length: count - floorCount },
      (_, i) => floorCount + i,
    );

    expect(solidAt(sceneCollision(centers, count, opacities, 0), [1.0, 1.0, 0.3])).toBe(true);
    const without = sceneCollision(centers, count, opacities, 0, { exclude: owned });
    expect(solidAt(without, [1.0, 1.0, 0.3])).toBe(false);
    expect(without.length).toBeGreaterThan(0); // the floor survives
  });

  it("stops at the room height, so sky floaters cost nothing", () => {
    const points: number[] = [];
    slab(points, [0, 0, 0.01], [2, 2, 0.02]);
    slab(points, [0, 0, 8], [0.4, 0.4, 0.4]); // a lump far above the ceiling
    const { centers, count, opacities } = cloudOf(points);

    const boxes = sceneCollision(centers, count, opacities, 0, { roomHeight: 3 });
    expect(solidAt(boxes, [0, 0, 8])).toBe(false);
  });

  it("merges rather than emitting a geom per cell", () => {
    const points: number[] = [];
    slab(points, [0, 0, 0.01], [4, 4, 0.02]);
    const { centers, count, opacities } = cloudOf(points);

    const boxes = sceneCollision(centers, count, opacities, 0);
    const cells = (4 / SOLID_CELL) ** 2;
    expect(boxes.length).toBeLessThan(cells / 10);
  });
});
