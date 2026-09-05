/**
 * The scanned room, as collision geometry.
 *
 * Everything the user did NOT select is scenery, and scenery has to be solid. A splat stops
 * nothing — there is no reference to Gaussians anywhere in a collision driver — so a room
 * described only by a ground plane, four walls and a couple of detected worktops is a room in
 * which a thrown object passes straight through the cupboards, the sink and the chairs. That
 * is what "the wall is still not real, same as all other objects" means, and it is accurate:
 * on the kitchen capture the entire 2.3-million-splat room contributed SIX boxes.
 *
 * So the geometry is taken from the splats themselves. An 8 cm occupancy grid over the whole
 * room, greedily merged into boxes by the same merger the selection uses, gives the units,
 * the counter fronts, the appliances and the walls their real shape — including the alcoves
 * and the gaps under things, which a bounding box cannot express.
 *
 * Three things this deliberately does NOT do:
 *
 * - **It does not try to say what anything IS.** Every box is scenery with one friction and
 *   no mass. Naming things is the selection's job, and needs a model (see `voxels.ts`).
 * - **It does not include the physicalised object.** Its splats are excluded, or the object
 *   would collide with a frozen copy of itself and be launched across the room on the first
 *   step.
 * - **It does not replace the walls.** A scan has holes — the doorway the scanner walked
 *   through, the wall behind the camera — and the box round the room is the backstop that
 *   stops things escaping through them.
 */

import * as THREE from "three";

import { toBoxes, type Grid } from "../selection/voxels";
import type { Obstacle, Vec3 } from "../types/protocol";

/** Cell size for the static scene, metres. Coarser than a selection: this is a whole room. */
export const SOLID_CELL = 0.08;

export interface SolidOptions {
  cell?: number;
  /** Splats needed in a cell before it is solid. Rejects reconstruction haze and floaters. */
  minSplatsPerCell?: number;
  /** Splats fainter than this are haze, not surface. */
  minOpacity?: number;
  /** How far above the ground to stop. Beyond this is ceiling and sky-floaters. */
  roomHeight?: number;
  /** Splats owned by a physicalised object, which must not also be scenery. */
  exclude?: ArrayLike<number>;
  /**
   * Cap on geoms handed to the solver.
   *
   * Generous on purpose: hitting it does not degrade the room gracefully, it leaves a HOLE,
   * because the merger stops emitting boxes rather than covering the remainder more coarsely.
   * The desk capture needs 1157 at 8 cm and the first cap was 1024, which quietly deleted the
   * last part of the room the sweep reached. Measured cost in MuJoCo: 2000 static boxes
   * compile in 338 ms and still simulate at 77x real time, so the headroom is real.
   */
  maxBoxes?: number;
}

/**
 * Build static collision boxes from the splats.
 *
 * Returns world-axis-aligned obstacles, ready for the wire. The grid is world-aligned rather
 * than oriented, because scenery has no frame of its own to be oriented to — and an axis
 * grid is what lets the merger produce long flat slabs for walls and floors instead of
 * stair-stepping along them.
 */
export function sceneCollision(
  centers: Float32Array,
  count: number,
  opacities: Float32Array,
  groundHeight: number,
  options: SolidOptions = {},
): Obstacle[] {
  const {
    cell = SOLID_CELL,
    minSplatsPerCell = 6,
    minOpacity = 0.1,
    roomHeight = 3.2,
    exclude,
    maxBoxes = 4096,
  } = options;

  const skip = new Uint8Array(count);
  if (exclude) for (let k = 0; k < exclude.length; k++) skip[exclude[k]] = 1;

  // Extent from the 1st/99th percentile in x and y: floaters sit outside the room in every
  // direction, and a grid sized to the furthest one is mostly empty air.
  const [loX, hiX] = span(centers, count, 0, opacities, minOpacity);
  const [loY, hiY] = span(centers, count, 1, opacities, minOpacity);
  const loZ = groundHeight;
  const hiZ = groundHeight + roomHeight;

  const dims: [number, number, number] = [
    Math.max(1, Math.ceil((hiX - loX) / cell)),
    Math.max(1, Math.ceil((hiY - loY) / cell)),
    Math.max(1, Math.ceil((hiZ - loZ) / cell)),
  ];

  // A Grid in the layout `toBoxes` expects. `origin` is the LOW CORNER of cell (0,0,0),
  // measured from the frame's centre -- not the cell's midpoint. `toBoxes` builds a box as
  // `origin + index * cell + half`, so a half-cell offset here shifts every obstacle by 4 cm
  // and a worktop stops things 4 cm above itself.
  const centre = new THREE.Vector3((loX + hiX) / 2, (loY + hiY) / 2, (loZ + hiZ) / 2);
  const grid: Grid = {
    dims,
    origin: new THREE.Vector3(loX - centre.x, loY - centre.y, loZ - centre.z),
    cell,
    counts: new Int32Array(dims[0] * dims[1] * dims[2]),
    // Deliberately empty. `toBoxes` never reads it, and an index list for two million splats
    // is tens of megabytes held for nothing.
    members: new Map(),
  };

  for (let i = 0; i < count; i++) {
    if (skip[i] || opacities[i] < minOpacity) continue;
    const x = Math.floor((centers[i * 3] - loX) / cell);
    const y = Math.floor((centers[i * 3 + 1] - loY) / cell);
    const z = Math.floor((centers[i * 3 + 2] - loZ) / cell);
    if (x < 0 || y < 0 || z < 0 || x >= dims[0] || y >= dims[1] || z >= dims[2]) continue;
    grid.counts[(z * dims[1] + y) * dims[0] + x] += 1;
  }

  const solid = new Set<number>();
  for (let i = 0; i < grid.counts.length; i++) {
    if (grid.counts[i] >= minSplatsPerCell) solid.add(i);
  }

  return toBoxes(grid, solid, maxBoxes).map((b, i) => ({
    id: `solid_${i}`,
    kind: "solid" as const,
    position: [
      centre.x + b.center[0],
      centre.y + b.center[1],
      centre.z + b.center[2],
    ] as Vec3,
    halfExtents: b.halfExtents as Vec3,
  }));
}

/** Robust extent along one axis, over visible splats only. */
function span(
  centers: Float32Array,
  count: number,
  axis: 0 | 1 | 2,
  opacities: Float32Array,
  minOpacity: number,
): [number, number] {
  const v: number[] = [];
  for (let i = 0; i < count; i++) {
    if (opacities[i] >= minOpacity) v.push(centers[i * 3 + axis]);
  }
  if (v.length === 0) return [0, 0];
  v.sort((a, b) => a - b);
  return [v[Math.floor(v.length * 0.01)], v[Math.floor(v.length * 0.99)]];
}
