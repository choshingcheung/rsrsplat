/**
 * Closing the hole an object leaves behind.
 *
 * Lift a vest off a carpet and there is no carpet under it: the scanner never saw that floor,
 * so those Gaussians do not exist. Removal is therefore correct and still looks broken — a
 * vest-shaped void with the room visible through it, right where the eye is already pointed.
 *
 * The fix is not to remove less. Removing less leaves fragments of the vest behind, and
 * removing MORE only makes the void larger. The hole has to be filled.
 *
 * What makes that tractable is that the missing surface is almost always flat and already
 * measured — it is the floor or the worktop the object was resting on, and its plane came out
 * of the same RANSAC fit that decided which way is up. So this does not invent geometry. It
 * copies the surface's own splats inward across the gap: nearest-neighbour propagation of
 * colour, scale and orientation, which is an inpaint by diffusion and reads as carpet because
 * it IS carpet, sampled from a few centimetres away.
 *
 * Deliberately not attempted:
 *
 * - **Anything not flat.** A hole in a bookshelf is not a plane and this will not pretend
 *   otherwise; it fills nothing and leaves the void, which is honest.
 * - **The object's own back.** That is a different problem with a different answer (a
 *   generated mesh), and conflating them produces a smear that is neither.
 */

import * as THREE from "three";
import { PackedSplats } from "@sparkjsdev/spark";

import type { Obstacle, Vec3 } from "../types/protocol";
import type { SplatCloud } from "./splats";

/** Cell size for the patch, metres. Fine enough that the seam does not read as a grid. */
export const PATCH_CELL = 0.025;

export interface HealOptions {
  cell?: number;
  /** How far from the plane a splat still counts as part of the surface. */
  band?: number;
  /** How far outside the hole to look for surviving surface to copy from. */
  margin?: number;
  /** Give up rather than fill a hole with no surface around it at all. */
  minDonors?: number;
  /** Half-thickness of the collision slab under the patch, metres. */
  slab?: number;
  /** Prefix for generated obstacle ids, so two patches cannot collide in the namespace. */
  id?: string;
}

export interface Patch {
  /** Ready to be merged into the static cloud. */
  splats: PackedSplats;
  count: number;
  /**
   * Collision for the filled area.
   *
   * Filling the hole visually and not physically is the worst of both: the floor looks
   * whole and things drop through it. That matters most exactly where it is least obvious
   * -- an object lifted off a DESK leaves a hole at desk height, and without this the next
   * thing set down there falls to the floor through a surface you can see.
   */
  obstacles: Obstacle[];
  /** False when there was no flat surface to copy from, and nothing was filled. */
  healed: boolean;
}

/**
 * Build the patch that closes the hole left by `removed`.
 *
 * `surfaceZ` is the height of the surface the object was standing on — the floor, usually.
 * Only splats within `band` of it are treated as donors, so a table leg beside the hole does
 * not get smeared across the floor.
 */
export function healSurface(
  cloud: SplatCloud,
  removed: ArrayLike<number>,
  surfaceZ: number,
  options: HealOptions = {},
): Patch {
  const {
    cell = PATCH_CELL,
    band = 0.06,
    margin = 0.35,
    minDonors = 12,
    slab = 0.02,
    id = "patch",
  } = options;

  const empty: Patch = { splats: new PackedSplats(), count: 0, obstacles: [], healed: false };
  if (removed.length === 0) return empty;

  // The hole's own footprint, from the splats that left.
  let loX = Infinity;
  let hiX = -Infinity;
  let loY = Infinity;
  let hiY = -Infinity;
  const gone = new Uint8Array(cloud.count);
  for (let k = 0; k < removed.length; k++) {
    const i = removed[k];
    gone[i] = 1;
    const x = cloud.centers[i * 3];
    const y = cloud.centers[i * 3 + 1];
    if (x < loX) loX = x;
    if (x > hiX) hiX = x;
    if (y < loY) loY = y;
    if (y > hiY) hiY = y;
  }

  // The grid covers the footprint plus a margin, because the donors live outside the hole.
  const originX = loX - margin;
  const originY = loY - margin;
  const nx = Math.max(1, Math.ceil((hiX - loX + margin * 2) / cell));
  const ny = Math.max(1, Math.ceil((hiY - loY + margin * 2) / cell));
  const at = (gx: number, gy: number) => gy * nx + gx;

  // Donors: surviving splats lying in the surface. One pass over the packed cloud, because
  // colour, scale and orientation all have to come across for the patch to look like floor
  // rather than like flat paint.
  const donor = new Int32Array(nx * ny).fill(-1);
  const colour: number[] = [];
  const scale: number[] = [];
  const quat: number[] = [];
  const alpha: number[] = [];
  let donors = 0;

  cloud.packed.forEachSplat((i, c, s, q, opacity, col) => {
    if (gone[i] || opacity < 0.2) return;
    if (Math.abs(c.z - surfaceZ) > band) return;
    const gx = Math.floor((c.x - originX) / cell);
    const gy = Math.floor((c.y - originY) / cell);
    if (gx < 0 || gy < 0 || gx >= nx || gy >= ny) return;
    const k = at(gx, gy);
    if (donor[k] >= 0) return; // one donor per cell is enough, and keeps this a single pass
    donor[k] = donors;
    colour.push(col.r, col.g, col.b);
    scale.push(s.x, s.y, s.z);
    quat.push(q.x, q.y, q.z, q.w);
    alpha.push(opacity);
    donors += 1;
  });

  if (donors < minDonors) return empty;

  // Which cells are hole: inside the footprint, and with no surviving surface in them.
  const holeLoX = Math.floor((loX - originX) / cell);
  const holeHiX = Math.min(nx - 1, Math.ceil((hiX - originX) / cell));
  const holeLoY = Math.floor((loY - originY) / cell);
  const holeHiY = Math.min(ny - 1, Math.ceil((hiY - originY) / cell));

  const fill: number[] = [];
  for (let gy = holeLoY; gy <= holeHiY; gy++) {
    for (let gx = holeLoX; gx <= holeHiX; gx++) {
      if (donor[at(gx, gy)] < 0) fill.push(at(gx, gy));
    }
  }
  if (fill.length === 0) return empty;

  // Nearest-neighbour propagation outward from the donors. Breadth-first over the whole
  // grid, so every empty cell ends up carrying the attributes of the CLOSEST real surface
  // splat rather than an average of everything nearby -- an average is grey mush, and grey
  // mush is exactly what reads as a patch.
  const source = Int32Array.from(donor);
  let frontier: number[] = [];
  for (let k = 0; k < source.length; k++) if (source[k] >= 0) frontier.push(k);

  while (frontier.length) {
    const next: number[] = [];
    for (const k of frontier) {
      const gx = k % nx;
      const gy = (k - gx) / nx;
      for (const [dx, dy] of [[1, 0], [-1, 0], [0, 1], [0, -1]]) {
        const ax = gx + dx;
        const ay = gy + dy;
        if (ax < 0 || ay < 0 || ax >= nx || ay >= ny) continue;
        const a = at(ax, ay);
        if (source[a] >= 0) continue;
        source[a] = source[k];
        next.push(a);
      }
    }
    frontier = next;
  }

  const splats = new PackedSplats();
  const centre = new THREE.Vector3();
  const scales = new THREE.Vector3();
  const rotation = new THREE.Quaternion();
  const col = new THREE.Color();
  let count = 0;

  for (const k of fill) {
    const d = source[k];
    if (d < 0) continue; // unreachable: the flood fills every cell once there is one donor
    const gx = k % nx;
    const gy = (k - gx) / nx;
    splats.pushSplat(
      centre.set(originX + (gx + 0.5) * cell, originY + (gy + 0.5) * cell, surfaceZ),
      scales.set(scale[d * 3], scale[d * 3 + 1], scale[d * 3 + 2]),
      rotation.set(quat[d * 4], quat[d * 4 + 1], quat[d * 4 + 2], quat[d * 4 + 3]),
      alpha[d],
      col.setRGB(colour[d * 3], colour[d * 3 + 1], colour[d * 3 + 2]),
    );
    count += 1;
  }

  return {
    splats,
    count,
    obstacles: mergeBoxes(fill, nx, originX, originY, cell, surfaceZ, slab, id),
    healed: count > 0,
  };
}

/**
 * The filled cells, as as few axis-aligned boxes as a greedy sweep can manage.
 *
 * One geom per cell would be hundreds of boxes for a patch a person reads as "the floor",
 * and every one of them is a pair the solver considers. Runs are grown in x and then in y,
 * which for a roughly convex hole collapses to a handful.
 */
function mergeBoxes(
  fill: number[],
  nx: number,
  originX: number,
  originY: number,
  cell: number,
  surfaceZ: number,
  slab: number,
  id: string,
): Obstacle[] {
  const open = new Set(fill);
  const boxes: Obstacle[] = [];

  for (const start of fill) {
    if (!open.has(start)) continue;
    const gx = start % nx;
    const gy = (start - gx) / nx;

    let ex = gx;
    while (open.has(gy * nx + ex + 1)) ex += 1;

    let ey = gy;
    grow: for (;;) {
      for (let x = gx; x <= ex; x++) if (!open.has((ey + 1) * nx + x)) break grow;
      ey += 1;
    }

    for (let y = gy; y <= ey; y++) for (let x = gx; x <= ex; x++) open.delete(y * nx + x);

    const halfX = ((ex - gx + 1) * cell) / 2;
    const halfY = ((ey - gy + 1) * cell) / 2;
    boxes.push({
      id: `${id}_${boxes.length}`,
      // `surface` and not `solid`: this is structure the room is missing, not scan geometry,
      // and the service must never carve it away as part of an object.
      kind: "surface",
      position: [originX + gx * cell + halfX, originY + gy * cell + halfY, surfaceZ - slab] as Vec3,
      halfExtents: [halfX, halfY, slab] as Vec3,
    });
  }

  return boxes;
}

/** Append one packed cloud onto another, in place. */
export function mergeInto(target: PackedSplats, extra: PackedSplats): void {
  const centre = new THREE.Vector3();
  const scales = new THREE.Vector3();
  const rotation = new THREE.Quaternion();
  const colour = new THREE.Color();
  extra.forEachSplat((_i, c, s, q, opacity, col) => {
    target.pushSplat(
      centre.copy(c),
      scales.copy(s),
      rotation.copy(q),
      opacity,
      colour.copy(col),
    );
  });
}
