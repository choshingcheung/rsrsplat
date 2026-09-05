/**
 * Binding: the round trip that has to be exact, and the part assignment that has to be total.
 *
 * The round trip is the whole of A7 in one assertion. Bake a splat into a body's frame,
 * give the body back its pose, and the splat must land exactly where it started. If it does
 * not, every splat on that body sits beside its collision box — which looks like a rendering
 * artefact and is really a missing multiplication.
 *
 * Translated from the prototype's `tests/test_binding.py`, which checked the rotation
 * composition against MuJoCo's own `mju_mulQuat` rather than trusting the algebra. There is
 * no `mju_mulQuat` in a browser, so the reference values come from the same fixture the
 * Python side tests against.
 */

import * as THREE from "three";
import { describe, expect, it } from "vitest";

import { assignParts, easePose, applyPose, inverseOf, toBodyLocal } from "./binding";
import type { SplatCloud } from "./splats";
import type { MeasuredFrame } from "../selection/frame";
import { toThree, toWire } from "../net/quaternion";
import type { PhysicsPart, PoseUpdate } from "../types/protocol";

function pose(
  bodyName: string,
  position: [number, number, number],
  orientation: [number, number, number, number] = [1, 0, 0, 0],
): PoseUpdate {
  return { bodyName, position, orientation };
}

/** A 30-degree yaw about z, the same rotation the contract fixtures use. */
const YAW30: [number, number, number, number] = [
  Math.cos(Math.PI / 12),
  0,
  0,
  Math.sin(Math.PI / 12),
];

// ---- the quaternion boundary --------------------------------------------------------------

describe("the wire/Three.js quaternion boundary", () => {
  it("moves the scalar part from the front to the back", () => {
    // A swapped quaternion is still unit length, so every sanity check passes and the object
    // simply sits at a strange angle. This is the assertion that says which end w is on.
    const q = toThree(YAW30);
    expect(q.w).toBeCloseTo(Math.cos(Math.PI / 12), 12);
    expect(q.z).toBeCloseTo(Math.sin(Math.PI / 12), 12);
    expect(q.x).toBe(0);
  });

  it("round-trips", () => {
    expect(toWire(toThree(YAW30))).toEqual([...YAW30]);
  });

  it("rotates a point the way MuJoCo would", () => {
    // +x yawed 30 degrees about +z.
    const v = new THREE.Vector3(1, 0, 0).applyQuaternion(toThree(YAW30));
    expect(v.x).toBeCloseTo(Math.cos(Math.PI / 6), 9);
    expect(v.y).toBeCloseTo(Math.sin(Math.PI / 6), 9);
  });
});

// ---- the round trip -----------------------------------------------------------------------

describe("baking a splat into a body's frame", () => {
  it("returns it exactly where it started once the body's pose is reapplied", () => {
    const initial = pose("door", [1.05, -0.14, -1.42], YAW30);
    const body = inverseOf(initial);

    const world = new THREE.Vector3(1.3, 0.2, -0.9);
    const spin = new THREE.Quaternion(0.1, 0.2, 0.3, 0.927).normalize();

    const local = new THREE.Vector3();
    const localSpin = new THREE.Quaternion();
    toBodyLocal(world, spin, body, local, localSpin);

    // What the renderer does: the mesh transform IS the pose, applied to the local splat.
    const back = local.clone().applyQuaternion(toThree(initial.orientation));
    back.add(new THREE.Vector3(...initial.position));

    expect(back.x).toBeCloseTo(world.x, 12);
    expect(back.y).toBeCloseTo(world.y, 12);
    expect(back.z).toBeCloseTo(world.z, 12);
  });

  it("carries the splat's own orientation through, not just its position", () => {
    // Rotating only the position leaves every ellipsoid pointing the way it did in world
    // space, so a door's splats smear sideways as it swings.
    const initial = pose("door", [0, 0, 0], YAW30);
    const body = inverseOf(initial);
    const spin = new THREE.Quaternion(0, 0, 0, 1);

    const local = new THREE.Vector3();
    const localSpin = new THREE.Quaternion();
    toBodyLocal(new THREE.Vector3(1, 0, 0), spin, body, local, localSpin);

    // A splat with no rotation of its own, on a body yawed 30 degrees, must come out yawed
    // -30 in the body's frame, so that reapplying the body's rotation cancels.
    const restored = localSpin.clone().premultiply(toThree(initial.orientation));
    expect(restored.angleTo(spin)).toBeCloseTo(0, 9);
  });

  it("binds against the body's initial pose, not the identity", () => {
    // The detail that decides whether a rotated object's splats snap on the first frame.
    const rotated = inverseOf(pose("shell", [5, 5, 5], YAW30));
    const identity = inverseOf(pose("shell", [0, 0, 0]));

    const world = new THREE.Vector3(6, 5, 5);
    const a = new THREE.Vector3();
    const b = new THREE.Vector3();
    const q = new THREE.Quaternion();
    toBodyLocal(world, new THREE.Quaternion(), rotated, a, q);
    toBodyLocal(world, new THREE.Quaternion(), identity, b, q);

    expect(a.distanceTo(b)).toBeGreaterThan(1);
  });
});

// ---- applying poses -----------------------------------------------------------------------

describe("applying a pose to a mesh", () => {
  it("sets position and orientation from the wire", () => {
    const object = new THREE.Object3D();
    applyPose(object, pose("x", [1, 2, 3], YAW30));

    expect(object.position.toArray()).toEqual([1, 2, 3]);
    expect(object.quaternion.w).toBeCloseTo(YAW30[0], 12);
    expect(object.quaternion.z).toBeCloseTo(YAW30[3], 12);
  });

  it("eases toward a target rather than snapping to it", () => {
    // 30 Hz of physics snapped straight onto 60 fps reads as judder, which looks like a low
    // frame rate rather than like a coarse update.
    const object = new THREE.Object3D();
    applyPose(object, pose("x", [0, 0, 0]));
    easePose(object, pose("x", [0, 0, 10]), 0.5);

    expect(object.position.z).toBeCloseTo(5, 9);
  });

  it("converges on the target when eased repeatedly", () => {
    const object = new THREE.Object3D();
    applyPose(object, pose("x", [0, 0, 0]));
    for (let i = 0; i < 40; i++) easePose(object, pose("x", [0, 0, 10]), 0.35);
    expect(object.position.z).toBeCloseTo(10, 6);
  });
});

// ---- part assignment ----------------------------------------------------------------------

function frameAt(centroid: [number, number, number], half: [number, number, number]): MeasuredFrame {
  return {
    centroid: new THREE.Vector3(...centroid),
    front: new THREE.Vector3(1, 0, 0),
    left: new THREE.Vector3(0, 1, 0),
    up: new THREE.Vector3(0, 0, 1),
    halfExtents: new THREE.Vector3(...half),
  };
}

function cloudOf(points: [number, number, number][]): SplatCloud {
  const centers = new Float32Array(points.length * 3);
  points.forEach((p, i) => centers.set(p, i * 3));
  return {
    packed: null as never, // assignParts only reads centers
    centers,
    opacities: new Float32Array(points.length).fill(1),
    count: points.length,
  };
}

function part(bodyName: string, splatSubset: PhysicsPart["splatSubset"]): PhysicsPart {
  return {
    bodyName,
    jointType: splatSubset === "all" ? "fixed" : "hinge",
    range: null,
    splatSubset,
    initialPose: pose(bodyName, [0, 0, 0]),
  };
}

describe("assigning splats to parts", () => {
  const frame = frameAt([0, 0, 0], [1, 1, 1]);
  // shell claims everything; the door claims the front. Order is significant.
  const parts = [part("shell", "all"), part("door", "front")];

  it("gives a splat to the LAST part whose region contains it", () => {
    const cloud = cloudOf([
      [0.5, 0, 0], // front: the door
      [-0.5, 0, 0], // back: only the shell claims it
    ]);
    const owned = assignParts(cloud, [0, 1], frame, parts);

    expect(owned.get("door")).toEqual([0]);
    expect(owned.get("shell")).toEqual([1]);
  });

  it("assigns every splat exactly once", () => {
    // A splat claimed twice is drawn twice; a splat claimed by nobody vanishes when the
    // static scene has its hole cut.
    const points: [number, number, number][] = [];
    for (let i = 0; i < 200; i++) {
      points.push([Math.sin(i) * 0.9, Math.cos(i * 1.7) * 0.9, Math.sin(i * 0.3) * 0.9]);
    }
    const cloud = cloudOf(points);
    const owned = assignParts(cloud, [...points.keys()], frame, parts);

    const total = [...owned.values()].reduce((n, list) => n + list.length, 0);
    const unique = new Set([...owned.values()].flat());
    expect(total).toBe(points.length);
    expect(unique.size).toBe(points.length);
  });

  it("leaves a part with no splats rather than failing", () => {
    const cloud = cloudOf([[-0.5, 0, 0]]);
    const owned = assignParts(cloud, [0], frame, parts);
    expect(owned.get("door")).toEqual([]);
    expect(owned.get("shell")).toEqual([0]);
  });

  it("works in the frame's own axes, not the world's", () => {
    // A selection rotated 90 degrees about up: its "front" is world +y.
    const rotated: MeasuredFrame = {
      centroid: new THREE.Vector3(0, 0, 0),
      front: new THREE.Vector3(0, 1, 0),
      left: new THREE.Vector3(-1, 0, 0),
      up: new THREE.Vector3(0, 0, 1),
      halfExtents: new THREE.Vector3(1, 1, 1),
    };
    const cloud = cloudOf([
      [0, 0.5, 0], // in front, in the frame's terms
      [0.5, 0, 0], // to one side
    ]);
    const owned = assignParts(cloud, [0, 1], rotated, parts);

    expect(owned.get("door")).toEqual([0]);
    expect(owned.get("shell")).toEqual([1]);
  });
});
