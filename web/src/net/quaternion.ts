/**
 * The one place in this codebase where quaternion order changes.
 *
 * The wire carries **(w, x, y, z)**, MuJoCo's convention, because that is what MuJoCo's
 * `xquat` is and what a 3DGS PLY stores. Three.js `Quaternion.set` takes **(x, y, z, w)`**.
 *
 * Getting this wrong does not throw and does not look obviously broken. A swapped quaternion
 * is still unit length, so every sanity check passes; the object simply sits at a strange
 * angle that looks like a bad scan, or spins the wrong way about the wrong axis when a door
 * opens. It is on the list of things that cost an hour, and this file is why it should only
 * ever cost that hour once.
 *
 * Nothing else in `web/` may reorder a quaternion. If you find yourself indexing `[3]` to
 * get a scalar part somewhere else, that code belongs here.
 */

import * as THREE from "three";

import type { Quat } from "../types/protocol";

/** A wire quaternion (w, x, y, z) as a Three.js one (x, y, z, w). */
export function toThree(wire: Quat, into = new THREE.Quaternion()): THREE.Quaternion {
  return into.set(wire[1], wire[2], wire[3], wire[0]);
}

/** A Three.js quaternion (x, y, z, w) as a wire one (w, x, y, z). */
export function toWire(q: THREE.Quaternion): Quat {
  return [q.w, q.x, q.y, q.z];
}
