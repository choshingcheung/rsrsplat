/**
 * Reading a capture, and pulling a subset of it out into something movable.
 *
 * These two operations are why the renderer library was chosen, and they decide whether
 * selection (A5) and live binding (A7) are possible at all:
 *
 * 1. **Per-splat centres.** Selection projects every centre to screen space during a drag,
 *    so the positions have to be readable as a flat array, not hidden inside a GPU buffer.
 * 2. **Independently transformable subsets.** Once a body is physical, its splats must move
 *    with it while the rest of the room stays put. `SplatMesh` extends `THREE.Object3D`, so
 *    a subset built into its own mesh gets a position and quaternion that Three.js composes
 *    for free — which is the whole reason this architecture is easier than the desktop one,
 *    where every Gaussian had to be transformed by hand each frame.
 *
 * Verified against the real 371 MB playroom capture before any of the app was built:
 * 1,495,461 splats, matching the Python reference parser exactly, read in 0.34 s.
 *
 * **The packed format is lossy.** Positions round-trip through `pushSplat`/`getSplat` to
 * about 4e-4, which is sub-millimetre at room scale and irrelevant for physics, but it does
 * mean a subset's centres are not bit-identical to the originals. Selection keeps its own
 * `centers` array from the parse for that reason: measuring an object's extents from
 * re-read packed data would quietly bake the quantisation into the half-extents on the wire.
 */

import { PackedSplats, PlyReader } from "@sparkjsdev/spark";
import * as THREE from "three";

/** A parsed capture: the packed data the renderer wants, plus the centres selection needs. */
export interface SplatCloud {
  /** What `SplatMesh` renders. */
  packed: PackedSplats;
  /** `count * 3` floats, xyz per splat, in the order the file declared them. */
  centers: Float32Array;
  /** Alpha per splat, post-sigmoid. Selection ignores splats too faint to see. */
  opacities: Float32Array;
  count: number;
}

/**
 * Parse a binary little-endian 3DGS PLY.
 *
 * Spark applies the same four conventions the Python reference does — `exp` on the
 * log-space scales, `sigmoid` on the pre-sigmoid opacity, band-0 harmonics to linear RGB,
 * and (w, x, y, z) quaternions normalised — so the two agree to the digit on counts,
 * bounding box, scales and opacity. The one difference is colour: the reference clips to
 * [0, 1] and Spark does not, leaving values slightly outside for the shader to handle.
 */
export async function readPly(fileBytes: Uint8Array): Promise<SplatCloud> {
  const reader = new PlyReader({ fileBytes });
  await reader.parseHeader();

  const count = reader.numSplats;
  const centers = new Float32Array(count * 3);
  const opacities = new Float32Array(count);
  const packed = new PackedSplats();

  const centre = new THREE.Vector3();
  const scales = new THREE.Vector3();
  const rotation = new THREE.Quaternion();
  const colour = new THREE.Color();

  reader.parseSplats((i, x, y, z, sx, sy, sz, qx, qy, qz, qw, opacity, r, g, b) => {
    centers[i * 3] = x;
    centers[i * 3 + 1] = y;
    centers[i * 3 + 2] = z;
    opacities[i] = opacity;
    // Spark hands back (x, y, z, w), which is Three.js order and what Quaternion.set wants.
    // The WIRE is (w, x, y, z); that conversion lives in web/src/net/ and only applies to
    // body poses, never to splat data, which never crosses the socket at all.
    packed.pushSplat(
      centre.set(x, y, z),
      scales.set(sx, sy, sz),
      rotation.set(qx, qy, qz, qw),
      opacity,
      colour.setRGB(r, g, b),
    );
  });

  return { packed, centers, opacities, count };
}

/** The axis-aligned bounds of every splat centre. */
export function bounds(cloud: SplatCloud): { min: THREE.Vector3; max: THREE.Vector3 } {
  const min = new THREE.Vector3(Infinity, Infinity, Infinity);
  const max = new THREE.Vector3(-Infinity, -Infinity, -Infinity);
  for (let i = 0; i < cloud.count; i++) {
    const x = cloud.centers[i * 3];
    const y = cloud.centers[i * 3 + 1];
    const z = cloud.centers[i * 3 + 2];
    if (x < min.x) min.x = x;
    if (y < min.y) min.y = y;
    if (z < min.z) min.z = z;
    if (x > max.x) max.x = x;
    if (y > max.y) max.y = y;
    if (z > max.z) max.z = z;
  }
  return { min, max };
}

/**
 * Percentile bounds, which are what any measurement of a capture should actually use.
 *
 * A trained splat scene carries floaters — stray Gaussians far outside the room, left over
 * from reconstruction. On the playroom capture they inflate the raw bounding box threefold:
 * 34 units raw against 11 units for the 1st-to-99th percentile, which holds 94% of the
 * splats. Scaling a scene by its raw longest axis therefore makes the room three times too
 * small, and every mass and every settling time downstream is then wrong.
 */
export function robustBounds(
  cloud: SplatCloud,
  percentile = 1,
): { min: THREE.Vector3; max: THREE.Vector3 } {
  const axis = new Float32Array(cloud.count);
  const min = new THREE.Vector3();
  const max = new THREE.Vector3();
  const lo = Math.floor((cloud.count * percentile) / 100);
  const hi = Math.min(cloud.count - 1, cloud.count - 1 - lo);

  for (let a = 0; a < 3; a++) {
    for (let i = 0; i < cloud.count; i++) axis[i] = cloud.centers[i * 3 + a];
    axis.sort();
    min.setComponent(a, axis[lo]);
    max.setComponent(a, axis[hi]);
  }
  return { min, max };
}

/**
 * Build a new packed cloud from the splats at `indices`.
 *
 * This is what moves a physicalised object's splats out of the static scene and into a
 * group that follows a body. The source cloud is left alone: the indices stay in the
 * browser, and nothing about them ever reaches the physics service.
 */
export function subset(cloud: SplatCloud, indices: ArrayLike<number>): PackedSplats {
  const out = new PackedSplats();
  const centre = new THREE.Vector3();
  const scales = new THREE.Vector3();
  const rotation = new THREE.Quaternion();
  const colour = new THREE.Color();

  // forEachSplat is a single pass over the source, so mark what we want and take it as it
  // goes by. Random access through getSplat() would be a pass per index.
  const wanted = new Uint8Array(cloud.count);
  for (let i = 0; i < indices.length; i++) wanted[indices[i]] = 1;

  cloud.packed.forEachSplat((i, c, s, q, opacity, col) => {
    if (!wanted[i]) return;
    out.pushSplat(
      centre.copy(c),
      scales.copy(s),
      rotation.copy(q),
      opacity,
      colour.copy(col),
    );
  });
  return out;
}

/**
 * Rewrite the packed data into the aligned frame, so a cloud has ONE frame throughout.
 *
 * `alignScene` transforms `centers` — what selection measures against — but the packed data
 * the renderer draws is a separate copy. Leaving them in different frames and compensating
 * with a transform on the render mesh works right up until something reads the packed data
 * directly, which `bind` does: its splats then land at the capture's original scale and
 * orientation, somewhere else entirely, while the hole is cut correctly in the aligned
 * frame. The object appears to vanish.
 *
 * So the packed data is rewritten once at load and the mesh carries no transform. A rebuild
 * of 1.5M splats costs about a second, on top of a load that already takes several.
 */
export function applyAlignment(cloud: SplatCloud, matrix: THREE.Matrix4): SplatCloud {
  // decompose, not setFromRotationMatrix: the latter assumes an UNSCALED matrix, and this
  // one carries the scale that takes the capture to metres. Reading a rotation straight off
  // it gives a quaternion that is wrong by exactly that factor.
  const offset = new THREE.Vector3();
  const rotation = new THREE.Quaternion();
  const scaling = new THREE.Vector3();
  matrix.decompose(offset, rotation, scaling);
  // The scale applies to a splat's SIZE as well as its position, or a scene scaled to a
  // third comes back with splats three times too big for it.
  const scale = scaling.x;

  const out = new PackedSplats();
  const centre = new THREE.Vector3();
  const scales = new THREE.Vector3();
  const spin = new THREE.Quaternion();
  const colour = new THREE.Color();

  cloud.packed.forEachSplat((_i, c, s, q, opacity, col) => {
    out.pushSplat(
      centre.copy(c).applyMatrix4(matrix),
      scales.copy(s).multiplyScalar(scale),
      spin.copy(rotation).multiply(q),
      opacity,
      colour.copy(col),
    );
  });

  return { ...cloud, packed: out };
}

/** The centre of splat `i`, without allocating. */
export function centerOf(cloud: SplatCloud, i: number, into: THREE.Vector3): THREE.Vector3 {
  return into.set(cloud.centers[i * 3], cloud.centers[i * 3 + 1], cloud.centers[i * 3 + 2]);
}
