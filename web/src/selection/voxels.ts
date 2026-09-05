/**
 * A voxel grid over a selection, and the two things it makes possible.
 *
 * ## Read this before trusting the segmentation
 *
 * This module contains NO segmentation model. The grow below is connected-component region
 * growing on an occupancy grid: pure geometry, no semantics, nothing that knows what an
 * object IS. `voxels.capture.test.ts` measured what that does on the real scan:
 *
 *     a 0.5 m box on the densest object caught  13,174 splats
 *       grown at >= 1 splat / cell:            105,506 splats   (801%)
 *                >= 2:                          94,447          (717%)
 *                >= 4:                          91,944          (698%)
 *                >= 8:                          68,901          (523%)
 *
 * That is not a tuning failure. A room scan is ONE CONNECTED MASS -- an object touches the
 * surface it stands on, which touches the wall, which touches everything -- so under a purely
 * geometric rule "connected to" means "in the same room as". The surface cut below buys back
 * the floor and nothing else; walls, worktops and neighbouring objects all still conduct.
 *
 * Nor can a growth ratio separate the two cases: a rectangle that legitimately caught a third
 * of an object grows 3x, and the runaway above grew 5x to 8x. Those ranges overlap. MAX_GROWTH
 * is therefore a guard rail that makes a bad segmentation FAIL VISIBLY (`escaped`, caller falls
 * back to the rectangle) rather than a threshold that makes it work.
 *
 * Separating an object from the surface it rests on needs to know what an object is, which
 * means a model. The prototype said as much in `crop()`'s docstring -- "the Tier-1 stand-in
 * for a SAM 3 part mask". That is still the open item; see PLAN.md.
 *
 * ## What it is for meanwhile
 *
 * **Segmentation, when it holds.** A drag rectangle plus a depth filter selects a rectangular
 * chunk of the room: some of the object, the floor under it, a slice of the wall behind -- and
 * it *misses* whatever fell outside the box, the clipped corner, the handle sticking out. When
 * the object is well separated the grow recovers those and drops the disconnected wall
 * fragment. When it is not, it says so.
 *
 * **Collision geometry.** The load-bearing half, and independent of the above. The occupied
 * cells of whatever set is finally owned, greedily merged into boxes, give a body the object's
 * actual shape instead of a cuboid around it. A chair gets legs; a bottle gets a neck; things
 * tip onto a corner instead of landing flat. Boxes are already convex, so unlike a generated
 * mesh this needs no convex decomposition and no external service.
 */

import * as THREE from "three";

import type { MeasuredFrame } from "./frame";

/** Cell size in metres. 3 cm keeps a chair's legs separate without exploding the box count. */
export const CELL = 0.03;

/** How far outside the selection box the grow may reach, metres. */
const MARGIN = 0.35;

/** A cell must hold at least this many splats to count as solid. Rejects reconstruction haze. */
const MIN_SPLATS_PER_CELL = 2;

/** Cells within this of a detected surface are cut, so the grow cannot escape into the floor. */
const SURFACE_CUT = 0.045;

/**
 * How much larger than the selection a grown component may be before it is rejected.
 *
 * A selection that caught half an object legitimately doubles, so the threshold has to sit
 * above 2. A runaway measured 5x to 8x on the real capture. The gap between those is narrow,
 * and that narrowness is the point: this is a GUARD RAIL on a method that does not actually
 * work on a room scan, not a fix for it. See the module docstring.
 */
const MAX_GROWTH = 2.5;

/** A box in the selection's own frame: axes are front, left, up. */
export interface LocalBox {
  center: [number, number, number];
  halfExtents: [number, number, number];
}

export interface Grid {
  /** Cells along front, left, up. */
  dims: [number, number, number];
  /** Local-frame coordinate of cell (0,0,0)'s LOW CORNER: `toBoxes` adds half a cell. */
  origin: THREE.Vector3;
  cell: number;
  /** Splats per cell. */
  counts: Int32Array;
  /** Which splat indices landed in each cell, for reading a component back out. */
  members: Map<number, number[]>;
}

const index = (g: Grid, x: number, y: number, z: number) =>
  (z * g.dims[1] + y) * g.dims[0] + x;

/**
 * Voxelise everything near the selection, in the selection's own frame.
 *
 * Deliberately not limited to the selected splats: the grow needs to see the object's
 * clipped corner, which by definition was not selected.
 */
export function voxelise(
  centers: Float32Array,
  count: number,
  frame: MeasuredFrame,
  { cell = CELL, margin = MARGIN } = {},
): Grid {
  const reach = frame.halfExtents.clone().addScalar(margin);
  const dims: [number, number, number] = [
    Math.max(1, Math.ceil((reach.x * 2) / cell)),
    Math.max(1, Math.ceil((reach.y * 2) / cell)),
    Math.max(1, Math.ceil((reach.z * 2) / cell)),
  ];
  const origin = reach.clone().negate();

  const grid: Grid = {
    dims,
    origin,
    cell,
    counts: new Int32Array(dims[0] * dims[1] * dims[2]),
    members: new Map(),
  };

  const d = new THREE.Vector3();
  for (let i = 0; i < count; i++) {
    d.set(centers[i * 3], centers[i * 3 + 1], centers[i * 3 + 2]).sub(frame.centroid);
    const u = d.dot(frame.front);
    const v = d.dot(frame.left);
    const w = d.dot(frame.up);

    const x = Math.floor((u - origin.x) / cell);
    const y = Math.floor((v - origin.y) / cell);
    const z = Math.floor((w - origin.z) / cell);
    if (x < 0 || y < 0 || z < 0 || x >= dims[0] || y >= dims[1] || z >= dims[2]) continue;

    const at = index(grid, x, y, z);
    grid.counts[at] += 1;
    const bucket = grid.members.get(at);
    if (bucket) bucket.push(i);
    else grid.members.set(at, [i]);
  }
  return grid;
}

export interface GrowOptions {
  /** Heights of detected horizontal surfaces, in WORLD z. The grow is cut at each. */
  surfaces?: number[];
  minSplatsPerCell?: number;
  /** Refuse a component larger than this many cells; a runaway flood is not an object. */
  maxCells?: number;
  /**
   * Refuse a component more than this many times the size of the seed set.
   *
   * The budget that actually matters. Measured against the grid it is useless: on the real
   * capture a flood reached 5,195 of 79,464 cells -- nowhere near a grid-relative cap, and
   * eight times the splats the user selected.
   */
  maxGrowth?: number;
}

/**
 * The object, grown out of the selection.
 *
 * Seeds from cells the user's rectangle actually hit, then floods through connected solid
 * cells in six directions. Two things stop it running away:
 *
 * - **Surfaces are cut.** Cells straddling a detected floor or worktop are treated as empty,
 *   so an object standing on one is not connected to it. Without this the flood leaves
 *   through the floor and returns with the entire room.
 * - **A cell budget.** If it still escapes, the component is rejected and the caller falls
 *   back to the plain selection, because a segmentation that ate the room is worse than none.
 */
export function grow(
  grid: Grid,
  centers: Float32Array,
  seeds: ArrayLike<number>,
  frame: MeasuredFrame,
  options: GrowOptions = {},
): { cells: Set<number>; indices: Uint32Array; escaped: boolean } {
  const minSplats = options.minSplatsPerCell ?? MIN_SPLATS_PER_CELL;
  const maxCells = options.maxCells ?? Math.floor(grid.counts.length * 0.5);
  const maxGrowth = options.maxGrowth ?? MAX_GROWTH;

  const solid = new Uint8Array(grid.counts.length);
  for (let i = 0; i < grid.counts.length; i++) {
    solid[i] = grid.counts[i] >= minSplats ? 1 : 0;
  }
  cutSurfaces(grid, frame, options.surfaces ?? [], solid);

  // Seed from the cells the rectangle hit, so growth starts where the user pointed.
  const seedCells = new Set<number>();
  const scratch = new THREE.Vector3();
  for (let k = 0; k < seeds.length; k++) {
    const at = cellOf(grid, centers, frame, seeds[k], scratch);
    if (at >= 0 && solid[at]) seedCells.add(at);
  }

  const cells = new Set<number>();
  const queue = [...seedCells];
  const [nx, ny] = grid.dims;
  let escaped = false;

  while (queue.length) {
    const at = queue.pop()!;
    if (cells.has(at)) continue;
    cells.add(at);
    if (cells.size > maxCells) {
      escaped = true;
      break;
    }

    const x = at % nx;
    const y = Math.floor(at / nx) % ny;
    const z = Math.floor(at / (nx * ny));
    for (const [dx, dy, dz] of NEIGHBOURS) {
      const [ax, ay, az] = [x + dx, y + dy, z + dz];
      if (ax < 0 || ay < 0 || az < 0) continue;
      if (ax >= grid.dims[0] || ay >= grid.dims[1] || az >= grid.dims[2]) continue;
      const next = index(grid, ax, ay, az);
      if (solid[next] && !cells.has(next)) queue.push(next);
    }
  }

  const indices: number[] = [];
  if (!escaped) {
    for (const at of cells) {
      const bucket = grid.members.get(at);
      if (bucket) indices.push(...bucket);
    }
  }

  // The check that a grid-relative budget missed entirely. A room scan is ONE CONNECTED
  // MASS -- an object touches the surface it stands on, which touches the wall, which
  // touches everything -- so a flood that is not stopped by geometry alone will happily
  // return most of the room while looking, cell-count-wise, perfectly reasonable.
  if (!escaped && seeds.length > 0 && indices.length > seeds.length * maxGrowth) {
    return { cells: new Set(), indices: new Uint32Array(0), escaped: true };
  }

  return { cells, indices: Uint32Array.from(indices), escaped };
}

const NEIGHBOURS: [number, number, number][] = [
  [1, 0, 0], [-1, 0, 0], [0, 1, 0], [0, -1, 0], [0, 0, 1], [0, 0, -1],
];

/** Which cell a splat is in, or -1 if it is outside the grid. */
function cellOf(
  grid: Grid,
  centers: Float32Array,
  frame: MeasuredFrame,
  splat: number,
  scratch: THREE.Vector3,
): number {
  scratch
    .set(centers[splat * 3], centers[splat * 3 + 1], centers[splat * 3 + 2])
    .sub(frame.centroid);
  const x = Math.floor((scratch.dot(frame.front) - grid.origin.x) / grid.cell);
  const y = Math.floor((scratch.dot(frame.left) - grid.origin.y) / grid.cell);
  const z = Math.floor((scratch.dot(frame.up) - grid.origin.z) / grid.cell);
  if (x < 0 || y < 0 || z < 0) return -1;
  if (x >= grid.dims[0] || y >= grid.dims[1] || z >= grid.dims[2]) return -1;
  return (z * grid.dims[1] + y) * grid.dims[0] + x;
}

/** Treat cells straddling a detected surface as empty, so a grow cannot leave through it. */
function cutSurfaces(
  grid: Grid,
  frame: MeasuredFrame,
  surfaces: number[],
  solid: Uint8Array,
): void {
  if (!surfaces.length) return;
  const [nx, ny, nz] = grid.dims;
  const point = new THREE.Vector3();

  for (let z = 0; z < nz; z++) {
    for (let y = 0; y < ny; y++) {
      for (let x = 0; x < nx; x++) {
        const at = index(grid, x, y, z);
        if (!solid[at]) continue;
        // Cell centre back into world coordinates, to compare against a world height.
        const local = new THREE.Vector3(
          grid.origin.x + (x + 0.5) * grid.cell,
          grid.origin.y + (y + 0.5) * grid.cell,
          grid.origin.z + (z + 0.5) * grid.cell,
        );
        point
          .copy(frame.centroid)
          .addScaledVector(frame.front, local.x)
          .addScaledVector(frame.left, local.y)
          .addScaledVector(frame.up, local.z);
        for (const height of surfaces) {
          if (Math.abs(point.z - height) < SURFACE_CUT) {
            solid[at] = 0;
            break;
          }
        }
      }
    }
  }
}

/**
 * Merge occupied cells into as few boxes as possible.
 *
 * Greedy: take an unused cell, extend along front while the whole row is free, then along
 * left while the whole slab is, then along up. Not optimal, and optimal is NP-hard and worth
 * nothing here -- what matters is that a chair comes out as a few dozen boxes rather than
 * two thousand, because every box is a geom MuJoCo has to consider.
 */
export function toBoxes(grid: Grid, cells: Set<number>, maxBoxes = 96): LocalBox[] {
  const [nx, ny, nz] = grid.dims;
  const used = new Uint8Array(grid.counts.length);
  const boxes: LocalBox[] = [];

  const filled = (x: number, y: number, z: number) => {
    const at = index(grid, x, y, z);
    return cells.has(at) && !used[at];
  };

  for (let z = 0; z < nz && boxes.length < maxBoxes; z++) {
    for (let y = 0; y < ny && boxes.length < maxBoxes; y++) {
      for (let x = 0; x < nx && boxes.length < maxBoxes; x++) {
        if (!filled(x, y, z)) continue;

        let ex = x;
        while (ex + 1 < nx && filled(ex + 1, y, z)) ex += 1;

        let ey = y;
        grow_y: while (ey + 1 < ny) {
          for (let sx = x; sx <= ex; sx++) if (!filled(sx, ey + 1, z)) break grow_y;
          ey += 1;
        }

        let ez = z;
        grow_z: while (ez + 1 < nz) {
          for (let sy = y; sy <= ey; sy++) {
            for (let sx = x; sx <= ex; sx++) if (!filled(sx, sy, ez + 1)) break grow_z;
          }
          ez += 1;
        }

        for (let sz = z; sz <= ez; sz++) {
          for (let sy = y; sy <= ey; sy++) {
            for (let sx = x; sx <= ex; sx++) used[index(grid, sx, sy, sz)] = 1;
          }
        }

        const half: [number, number, number] = [
          ((ex - x + 1) * grid.cell) / 2,
          ((ey - y + 1) * grid.cell) / 2,
          ((ez - z + 1) * grid.cell) / 2,
        ];
        boxes.push({
          center: [
            grid.origin.x + x * grid.cell + half[0],
            grid.origin.y + y * grid.cell + half[1],
            grid.origin.z + z * grid.cell + half[2],
          ],
          halfExtents: half,
        });
      }
    }
  }
  return boxes;
}
