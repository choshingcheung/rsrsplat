/**
 * Segmentation, on the real capture.
 *
 * The synthetic tests fill boxes on a regular lattice, so every cell is uniformly dense and
 * connectivity is guaranteed. A trained splat scene is neither: it is sparse in places, hazy
 * in others, and full of floaters. Whether the grow works there is a different question, and
 * it is the only question that matters.
 *
 * This prints what it found rather than only passing or failing, because "the removal is not
 * close to good" is a symptom with several possible causes and the numbers separate them.
 *
 * Skips cleanly without RSRSPLAT_CAPTURE.
 */

import { existsSync, readFileSync } from "node:fs";

import * as THREE from "three";
import { describe, expect, it } from "vitest";

import { measureFrame } from "./frame";
import { grow, toBoxes, voxelise, type Grid } from "./voxels";
import { alignScene, roomObstacles } from "../scene/ground";
import { readPly, type SplatCloud } from "../scene/splats";

const CAPTURE = process.env.RSRSPLAT_CAPTURE;
const haveCapture = Boolean(CAPTURE && existsSync(CAPTURE));

let cloud: SplatCloud;
let surfaces: number[] = [];
let densest = new THREE.Vector3();

/** Occupancy statistics: how connected is this cloud, really? */
function occupancy(grid: Grid, minSplats: number): { cells: number; total: number } {
  let cells = 0;
  for (let i = 0; i < grid.counts.length; i++) if (grid.counts[i] >= minSplats) cells += 1;
  return { cells, total: grid.counts.length };
}

/** A selection box around a point, as pick() would roughly produce. */
function boxAround(centre: THREE.Vector3, half: THREE.Vector3): Uint32Array {
  const out: number[] = [];
  for (let i = 0; i < cloud.count; i++) {
    const dx = Math.abs(cloud.centers[i * 3] - centre.x);
    const dy = Math.abs(cloud.centers[i * 3 + 1] - centre.y);
    const dz = Math.abs(cloud.centers[i * 3 + 2] - centre.z);
    if (dx <= half.x && dy <= half.y && dz <= half.z && cloud.opacities[i] >= 0.1) out.push(i);
  }
  return Uint32Array.from(out);
}

describe.skipIf(!haveCapture)("segmentation on a real capture", () => {
  it("loads and aligns", async () => {
    cloud = await readPly(new Uint8Array(readFileSync(CAPTURE!)));
    const alignment = alignScene(cloud);
    const obstacles = roomObstacles(cloud.centers, cloud.count, alignment.groundHeight);
    surfaces = [
      alignment.groundHeight,
      ...obstacles
        .filter((o) => o.kind === "surface")
        .map((o) => o.position[2] + o.halfExtents[2]),
    ];
    // eslint-disable-next-line no-console
    console.log(`\n  ${cloud.count.toLocaleString()} splats, surfaces at ${surfaces.map((s) => s.toFixed(2)).join(", ")}`);
    expect(cloud.count).toBeGreaterThan(0);
  }, 180_000);

  it("finds where the splats actually are", () => {
    // Guessing a probe point put it in empty air: the middle of a room is air, and splats
    // live on surfaces. So locate the densest lump above the floor and work from there.
    let lo = new THREE.Vector3(Infinity, Infinity, Infinity);
    let hi = new THREE.Vector3(-Infinity, -Infinity, -Infinity);
    for (let i = 0; i < cloud.count; i++) {
      if (cloud.opacities[i] < 0.1) continue;
      lo.min(new THREE.Vector3(cloud.centers[i * 3], cloud.centers[i * 3 + 1], cloud.centers[i * 3 + 2]));
      hi.max(new THREE.Vector3(cloud.centers[i * 3], cloud.centers[i * 3 + 1], cloud.centers[i * 3 + 2]));
    }

    // A coarse 10 cm grid over the whole room, counting only visible splats well clear of
    // the floor -- anything on the floor is the floor, not an object.
    const cell = 0.1;
    const floor = surfaces[0] ?? lo.z;
    const dims = [
      Math.ceil((hi.x - lo.x) / cell),
      Math.ceil((hi.y - lo.y) / cell),
      Math.ceil((hi.z - lo.z) / cell),
    ];
    const counts = new Int32Array(dims[0] * dims[1] * dims[2]);
    for (let i = 0; i < cloud.count; i++) {
      if (cloud.opacities[i] < 0.1) continue;
      const z = cloud.centers[i * 3 + 2];
      if (z < floor + 0.15) continue;
      const x = Math.floor((cloud.centers[i * 3] - lo.x) / cell);
      const y = Math.floor((cloud.centers[i * 3 + 1] - lo.y) / cell);
      const k = Math.floor((z - lo.z) / cell);
      if (x < 0 || y < 0 || k < 0 || x >= dims[0] || y >= dims[1] || k >= dims[2]) continue;
      counts[(k * dims[1] + y) * dims[0] + x] += 1;
    }

    let best = 0;
    let bestAt = 0;
    let occupied = 0;
    for (let i = 0; i < counts.length; i++) {
      if (counts[i] > 0) occupied += 1;
      if (counts[i] > best) {
        best = counts[i];
        bestAt = i;
      }
    }
    const bx = bestAt % dims[0];
    const by = Math.floor(bestAt / dims[0]) % dims[1];
    const bz = Math.floor(bestAt / (dims[0] * dims[1]));
    densest = new THREE.Vector3(
      lo.x + (bx + 0.5) * cell,
      lo.y + (by + 0.5) * cell,
      lo.z + (bz + 0.5) * cell,
    );

    // eslint-disable-next-line no-console
    console.log(
      [
        ``,
        `  room (visible splats)  ${lo.toArray().map((v) => v.toFixed(2)).join(", ")}`,
        `                      .. ${hi.toArray().map((v) => v.toFixed(2)).join(", ")}`,
        `  floor at              ${floor.toFixed(2)}`,
        `  10 cm cells occupied  ${occupied.toLocaleString()} of ${counts.length.toLocaleString()}`,
        `  densest cell          ${best.toLocaleString()} splats at ${densest.toArray().map((v) => v.toFixed(2)).join(", ")}`,
        ``,
      ].join(String.fromCharCode(10)),
    );
    expect(best).toBeGreaterThan(0);
  }, 120_000);

  it("says what the grow actually does there, at each threshold", () => {
    const half = new THREE.Vector3(0.25, 0.25, 0.25);
    const seeds = boxAround(densest, half);
    const frame = measureFrame(cloud.centers, seeds, new THREE.Vector3(0, 0, 1), new THREE.Vector3(0, -4, 1.5));
    const grid = voxelise(cloud.centers, cloud.count, frame);

    const density = [1, 2, 4, 8].map((m) => {
      const { cells, total } = occupancy(grid, m);
      return `    >=${String(m).padStart(2)} splats/cell: ${String(cells).padStart(6)} of ${total.toLocaleString()} cells occupied`;
    });

    const rows = [1, 2, 4, 8].map((minSplatsPerCell) => {
      const grown = grow(grid, cloud.centers, seeds, frame, { surfaces, minSplatsPerCell });
      const boxes = grown.escaped ? [] : toBoxes(grid, grown.cells);
      return (
        `    >=${String(minSplatsPerCell).padStart(2)}: ` +
        `${String(grown.indices.length).padStart(7)} splats ` +
        `(${((grown.indices.length / Math.max(1, seeds.length)) * 100).toFixed(0)}% of the ${seeds.length.toLocaleString()} selected), ` +
        `${String(grown.cells.size).padStart(5)} cells, ${String(boxes.length).padStart(3)} boxes` +
        (grown.escaped ? "   ESCAPED" : "")
      );
    });

    // eslint-disable-next-line no-console
    console.log(
      [
        `  a 0.5 m box on the densest lump caught ${seeds.length.toLocaleString()} visible splats`,
        `  grid ${grid.dims.join(" x ")}`,
        ...density,
        ``,
        `  grow:`,
        ...rows,
        ``,
      ].join(String.fromCharCode(10)),
    );
    expect(rows.length).toBe(4);
  }, 180_000);
});
