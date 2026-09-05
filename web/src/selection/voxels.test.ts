/**
 * Segmentation and collision shape, on objects whose answer is known.
 *
 * The two tests that carry the weight are `takes the whole object even when the rectangle
 * clipped it` and `does not escape through the floor`. Between them they are the difference
 * between "it removed a rectangular slab and left the handle behind" and "it removed the
 * object", which was the complaint this module exists to answer.
 */

import * as THREE from "three";
import { describe, expect, it } from "vitest";

import type { MeasuredFrame } from "./frame";
import { grow, toBoxes, voxelise, type LocalBox } from "./voxels";

/** An identity frame at the origin: front +x, left +y, up +z. */
function frameAt(
  centroid: [number, number, number],
  half: [number, number, number],
): MeasuredFrame {
  return {
    centroid: new THREE.Vector3(...centroid),
    front: new THREE.Vector3(1, 0, 0),
    left: new THREE.Vector3(0, 1, 0),
    up: new THREE.Vector3(0, 0, 1),
    halfExtents: new THREE.Vector3(...half),
  };
}

/** Fill a box with points on a regular lattice, so occupancy is unambiguous. */
function fill(
  out: number[],
  centre: [number, number, number],
  size: [number, number, number],
  step = 0.012,
): void {
  for (let x = -size[0] / 2; x <= size[0] / 2; x += step) {
    for (let y = -size[1] / 2; y <= size[1] / 2; y += step) {
      for (let z = -size[2] / 2; z <= size[2] / 2; z += step) {
        out.push(centre[0] + x, centre[1] + y, centre[2] + z);
      }
    }
  }
}

function cloud(points: number[]): { centers: Float32Array; count: number } {
  return { centers: Float32Array.from(points), count: points.length / 3 };
}

/** Indices of every splat inside an axis-aligned box: what a drag rectangle roughly gives. */
function inside(
  centers: Float32Array,
  count: number,
  lo: [number, number, number],
  hi: [number, number, number],
): Uint32Array {
  const out: number[] = [];
  for (let i = 0; i < count; i++) {
    const [x, y, z] = [centers[i * 3], centers[i * 3 + 1], centers[i * 3 + 2]];
    if (x >= lo[0] && x <= hi[0] && y >= lo[1] && y <= hi[1] && z >= lo[2] && z <= hi[2]) {
      out.push(i);
    }
  }
  return Uint32Array.from(out);
}

describe("voxelising", () => {
  it("reaches beyond the selection box, because the clipped corner is out there", () => {
    const frame = frameAt([0, 0, 0], [0.2, 0.2, 0.2]);
    const grid = voxelise(Float32Array.from([]), 0, frame);
    // 0.2 half-extent plus 0.35 margin, doubled, over 0.03 cells.
    expect(grid.dims[0]).toBeGreaterThan((0.2 * 2) / 0.03);
  });

  it("counts splats per cell", () => {
    const points: number[] = [];
    fill(points, [0, 0, 0], [0.1, 0.1, 0.1]);
    const { centers, count } = cloud(points);
    const grid = voxelise(centers, count, frameAt([0, 0, 0], [0.1, 0.1, 0.1]));
    expect([...grid.members.values()].reduce((n, b) => n + b.length, 0)).toBe(count);
  });
});

describe("growing the object out of a partial selection", () => {
  it("takes the whole object even when the rectangle clipped it", () => {
    // A 40 cm cube standing on a floor. The user's rectangle catches only its left half.
    const points: number[] = [];
    fill(points, [0, 0, 0.2], [0.4, 0.4, 0.4]);
    const objectCount = points.length / 3;
    fill(points, [0, 0, -0.02], [3, 3, 0.04], 0.03); // the floor it stands on

    const { centers, count } = cloud(points);
    const frame = frameAt([0, 0, 0.2], [0.2, 0.2, 0.2]);
    const clipped = inside(centers, count, [-0.2, -0.2, 0.05], [0.0, 0.2, 0.35]);

    expect(clipped.length).toBeLessThan(objectCount * 0.6); // genuinely a partial selection

    const grid = voxelise(centers, count, frame);
    const grown = grow(grid, centers, clipped, frame, { surfaces: [0] });

    expect(grown.escaped).toBe(false);
    // Recovers most of the cube from a selection that caught barely half of it.
    expect(grown.indices.length).toBeGreaterThan(objectCount * 0.85);
  });

  it("does not escape through the floor", () => {
    // Without the surface cut the flood leaves through the floor and returns with the room.
    const points: number[] = [];
    fill(points, [0, 0, 0.15], [0.3, 0.3, 0.3]);
    const objectCount = points.length / 3;
    fill(points, [0, 0, -0.02], [4, 4, 0.04], 0.03);

    const { centers, count } = cloud(points);
    const frame = frameAt([0, 0, 0.15], [0.15, 0.15, 0.15]);
    const seeds = inside(centers, count, [-0.15, -0.15, 0.05], [0.15, 0.15, 0.3]);

    const grown = grow(voxelise(centers, count, frame), centers, seeds, frame, {
      surfaces: [0],
    });
    expect(grown.escaped).toBe(false);
    expect(grown.indices.length).toBeLessThan(objectCount * 1.35);
  });

  it("reports an escape rather than returning the room", () => {
    // No surface declared, so nothing stops the flood. It must SAY so, because a
    // segmentation that ate the room is worse than no segmentation at all.
    const points: number[] = [];
    fill(points, [0, 0, 0.15], [0.3, 0.3, 0.3]);
    fill(points, [0, 0, 0.0], [4, 4, 0.06], 0.02);

    const { centers, count } = cloud(points);
    const frame = frameAt([0, 0, 0.15], [0.15, 0.15, 0.15]);
    const seeds = inside(centers, count, [-0.15, -0.15, 0.05], [0.15, 0.15, 0.3]);

    const grown = grow(voxelise(centers, count, frame), centers, seeds, frame, {
      surfaces: [],
      maxCells: 400,
    });
    expect(grown.escaped).toBe(true);
    expect(grown.indices.length).toBe(0);
  });

  it("drops a disconnected wall fragment the rectangle also caught", () => {
    // The frustum reaches past the object; a slice of wall behind it comes along. It is not
    // connected to the object, so it does not survive the grow.
    const points: number[] = [];
    fill(points, [0, 0, 0.15], [0.3, 0.3, 0.3]);
    const objectCount = points.length / 3;
    fill(points, [0.6, 0, 0.4], [0.04, 1.2, 0.8], 0.02); // a wall, 30 cm behind

    const { centers, count } = cloud(points);
    const frame = frameAt([0, 0, 0.15], [0.15, 0.15, 0.15]);
    const seeds = inside(centers, count, [-0.15, -0.4, 0.0], [0.7, 0.4, 0.5]);

    expect(seeds.length).toBeGreaterThan(objectCount); // the wall really was selected

    const grown = grow(voxelise(centers, count, frame), centers, seeds, frame, {
      surfaces: [0],
    });
    expect(grown.escaped).toBe(false);
    expect(grown.indices.length).toBeLessThan(objectCount * 1.2);
  });
});

describe("collision boxes", () => {
  function boxesFor(points: number[], frame: MeasuredFrame): LocalBox[] {
    const { centers, count } = cloud(points);
    const grid = voxelise(centers, count, frame);
    const seeds = Uint32Array.from({ length: count }, (_, i) => i);
    const grown = grow(grid, centers, seeds, frame, { surfaces: [] });
    return toBoxes(grid, grown.cells);
  }

  it("merges a solid cube into a handful of boxes, not thousands of cells", () => {
    // Every box is a geom the solver considers, so the merge is what makes this usable.
    const points: number[] = [];
    fill(points, [0, 0, 0], [0.3, 0.3, 0.3]);
    const boxes = boxesFor(points, frameAt([0, 0, 0], [0.15, 0.15, 0.15]));

    expect(boxes.length).toBeGreaterThan(0);
    expect(boxes.length).toBeLessThan(20);
  });

  it("gives a table legs instead of one slab", () => {
    // The whole point. A bounding box includes the air between the legs, so a chair cannot
    // be pushed under a table and nothing ever tips.
    const points: number[] = [];
    fill(points, [0, 0, 0.38], [0.6, 0.6, 0.04]); // top
    for (const [x, y] of [[-0.25, -0.25], [0.25, -0.25], [-0.25, 0.25], [0.25, 0.25]]) {
      fill(points, [x, y, 0.18], [0.05, 0.05, 0.36]);
    }
    const boxes = boxesFor(points, frameAt([0, 0, 0.2], [0.3, 0.3, 0.2]));

    // A point in the middle, under the top and between the legs, must be in NO box.
    const gap = new THREE.Vector3(0, 0, -0.05);
    const covered = boxes.some((b) =>
      Math.abs(gap.x - b.center[0]) <= b.halfExtents[0] &&
      Math.abs(gap.y - b.center[1]) <= b.halfExtents[1] &&
      Math.abs(gap.z - b.center[2]) <= b.halfExtents[2],
    );
    expect(covered).toBe(false);
  });

  it("covers every occupied cell exactly once", () => {
    // Overlapping boxes double a body's mass; missed cells leave a hole to fall through.
    const points: number[] = [];
    fill(points, [0, 0, 0], [0.2, 0.2, 0.2]);
    const { centers, count } = cloud(points);
    const frame = frameAt([0, 0, 0], [0.1, 0.1, 0.1]);
    const grid = voxelise(centers, count, frame);
    const seeds = Uint32Array.from({ length: count }, (_, i) => i);
    const grown = grow(grid, centers, seeds, frame, { surfaces: [] });
    const boxes = toBoxes(grid, grown.cells);

    const volume = boxes.reduce(
      (v, b) => v + 8 * b.halfExtents[0] * b.halfExtents[1] * b.halfExtents[2],
      0,
    );
    const cellVolume = grown.cells.size * grid.cell ** 3;
    expect(volume).toBeCloseTo(cellVolume, 6);
  });

  it("respects a box budget", () => {
    const points: number[] = [];
    // A shell: hollow, so the merge cannot collapse it into one slab.
    for (let i = 0; i < 3000; i++) {
      const a = (i / 3000) * Math.PI * 2;
      fill(points, [0.2 * Math.cos(a), 0.2 * Math.sin(a), 0], [0.02, 0.02, 0.3], 0.02);
    }
    const { centers, count } = cloud(points);
    const frame = frameAt([0, 0, 0], [0.25, 0.25, 0.15]);
    const grid = voxelise(centers, count, frame);
    const seeds = Uint32Array.from({ length: count }, (_, i) => i);
    const grown = grow(grid, centers, seeds, frame, { surfaces: [] });

    expect(toBoxes(grid, grown.cells, 24).length).toBeLessThanOrEqual(24);
  });
});
