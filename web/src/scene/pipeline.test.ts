/**
 * The whole client pipeline, headless.
 *
 * Selection through measurement through the service through binding, in one test, against
 * the in-browser service. Everything except the WebGL draw call runs here — Spark's
 * `SplatMesh` and `SplatEdit` construct fine without a GPU — so the gap this leaves is
 * "does it look right", not "does it work".
 *
 * Written after a session where typing a prompt did nothing and nothing said why. Each unit
 * along the path was green; what was missing was a test that ran them in order.
 */

import { SplatMesh } from "@sparkjsdev/spark";
import * as THREE from "three";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { bind, unbind } from "./binding";
import { alignScene } from "./ground";
import type { SplatCloud } from "./splats";
import { MockService } from "../net/mock";
import { measureFrame, toSelection } from "../selection/frame";
import { pick } from "../selection/pick";
import type { PhysicsObject, ServerMessage } from "../types/protocol";
import { PackedSplats } from "@sparkjsdev/spark";

const UP = new THREE.Vector3(0, 0, 1);

/**
 * A room: a wide floor slab with a crate standing on it.
 *
 * Both are needed. A cloud containing only the object cannot catch a selection that also
 * takes the floor, and the alignment step has nothing to find an up axis from.
 */
function room(): SplatCloud {
  const points: [number, number, number][] = [];
  let state = 3;
  const rand = () => ((state = (state * 1664525 + 1013904223) % 4294967296), state / 4294967296);

  // Floor slab at z = 0, six metres across.
  for (let i = 0; i < 6000; i++) {
    points.push([(rand() - 0.5) * 6, (rand() - 0.5) * 6, (rand() - 0.5) * 0.02]);
  }
  // Ceiling, so the layer score has two surfaces to stack along.
  for (let i = 0; i < 3000; i++) {
    points.push([(rand() - 0.5) * 6, (rand() - 0.5) * 6, 2.6 + (rand() - 0.5) * 0.02]);
  }
  // A 0.5 m crate sitting on the floor at the origin.
  for (let i = 0; i < 3000; i++) {
    points.push([(rand() - 0.5) * 0.5, (rand() - 0.5) * 0.5, 0.25 + (rand() - 0.5) * 0.5]);
  }

  const centers = new Float32Array(points.length * 3);
  const opacities = new Float32Array(points.length).fill(0.9);
  points.forEach((p, i) => centers.set(p, i * 3));

  const packed = new PackedSplats();
  const c = new THREE.Vector3();
  const s = new THREE.Vector3(0.01, 0.01, 0.01);
  const q = new THREE.Quaternion();
  const col = new THREE.Color(0.6, 0.6, 0.6);
  for (const p of points) packed.pushSplat(c.set(...p), s, q, 0.9, col);

  return { packed, centers, opacities, count: points.length };
}

/** A camera looking at the crate from across the room. */
function camera(): THREE.PerspectiveCamera {
  const cam = new THREE.PerspectiveCamera(55, 16 / 9, 0.05, 500);
  cam.up.copy(UP);
  cam.position.set(0, -3, 1.2);
  cam.lookAt(0, 0, 0.25);
  cam.updateMatrixWorld();
  cam.updateProjectionMatrix();
  return cam;
}

beforeEach(() => vi.useFakeTimers());
afterEach(() => vi.useRealTimers());

describe("the full path: select, describe, bind, move", () => {
  it("takes a crate from a drag rectangle to splats that fall", async () => {
    const cloud = room();

    // 1. Align. The service refuses a scene that is not metric and z-up.
    const alignment = alignScene(cloud);
    expect(alignment.up.z).toBeCloseTo(1, 6);

    // 2. Select. A rectangle over the middle of the view, where the crate is.
    const cam = camera();
    const { indices } = pick(cloud.centers, cloud.opacities, cloud.count, cam, {
      x0: -0.28,
      y0: -0.28,
      x1: 0.28,
      y1: 0.28,
    });
    expect(indices.length).toBeGreaterThan(200);

    // 3. Measure.
    const frame = measureFrame(cloud.centers, indices, UP, cam.position);
    const selection = toSelection("sel_1", frame, indices.length);
    expect(selection.halfExtents.every((v) => v > 0)).toBe(true);

    // 4. Describe it, through the service.
    const messages: ServerMessage[] = [];
    const service = new MockService({ onMessage: (m) => messages.push(m) });
    service.send({
      type: "scene.load",
      splatId: "room",
      splatCount: cloud.count,
      obstacles: [],
      world: {
        up: [alignment.up.x, alignment.up.y, alignment.up.z],
        groundHeight: alignment.groundHeight,
        sceneScale: 1,
      },
    });
    service.send({ type: "selection.commit", selection });
    service.send({
      type: "object.physicalize",
      selectionId: "sel_1",
      prompt: "a wooden crate, heavy",
    });

    const created = messages.find((m) => m.type === "object.created");
    expect(created, "the service must answer a physicalize").toBeDefined();
    const object = (created as { object: PhysicsObject }).object;
    expect(object.parts[0].jointType).toBe("free");

    // 5. Bind. This is the step that was throwing inside a socket handler.
    const staticMesh = new SplatMesh({ packedSplats: cloud.packed, editable: true });
    await staticMesh.initialized;

    const bound = bind(cloud, indices, frame, object);

    expect(bound.parts).toHaveLength(object.parts.length);
    const totalBound = bound.parts.reduce((n, p) => n + p.splatCount, 0);
    expect(totalBound, "every selected splat must belong to exactly one part").toBe(
      indices.length,
    );
    // Removal is EXACT: the remainder is the original cloud minus precisely these splats,
    // not minus a box that also swallowed the floor and the wall behind.
    expect(bound.removed).toHaveLength(indices.length);
    expect(bound.remaining.numSplats).toBe(cloud.count - indices.length);

    // 6. Move. The mesh transform IS the pose.
    const shell = bound.parts[0];
    const startZ = shell.mesh.position.z;
    service.send({ type: "sim.control", action: "play" });
    vi.advanceTimersByTime(2000);

    const batches = messages.filter((m) => m.type === "pose.batch");
    expect(batches.length).toBeGreaterThan(30);

    const last = batches.at(-1) as { poses: { bodyName: string; position: number[] }[] };
    const pose = last.poses.find((p) => p.bodyName === shell.bodyName);
    expect(pose, "the stream must carry the body this mesh is bound to").toBeDefined();
    expect(pose!.position[2]).toBeLessThan(startZ);

    service.stop();
    unbind(bound, new THREE.Object3D());
  });

  it("binds an articulated object's parts separately", async () => {
    const cloud = room();
    const cam = camera();
    const { indices } = pick(cloud.centers, cloud.opacities, cloud.count, cam, {
      x0: -0.28,
      y0: -0.28,
      x1: 0.28,
      y1: 0.28,
    });
    const frame = measureFrame(cloud.centers, indices, UP, cam.position);

    const messages: ServerMessage[] = [];
    const service = new MockService({ onMessage: (m) => messages.push(m) });
    service.send({
      type: "scene.load",
      splatId: "room",
      splatCount: cloud.count,
      obstacles: [],
      world: { up: [0, 0, 1], groundHeight: 0, sceneScale: 1 },
    });
    service.send({
      type: "selection.commit",
      selection: toSelection("sel_1", frame, indices.length),
    });
    service.send({
      type: "object.physicalize",
      selectionId: "sel_1",
      prompt: "a dishwasher, the door hinges at the bottom",
    });
    service.stop();

    const object = (messages.find((m) => m.type === "object.created") as { object: PhysicsObject })
      .object;
    expect(object.parts).toHaveLength(2);

    const staticMesh = new SplatMesh({ packedSplats: cloud.packed, editable: true });
    await staticMesh.initialized;
    const bound = bind(cloud, indices, frame, object);

    // Shell and door both get splats, and between them they get all of them.
    const counts = Object.fromEntries(bound.parts.map((p) => [p.bodyName, p.splatCount]));
    expect(Object.values(counts).reduce((a, b) => a + b, 0)).toBe(indices.length);
    for (const part of bound.parts) {
      expect(part.mesh.isObject3D).toBe(true);
    }
    const door = bound.parts.find((p) => p.bodyName.endsWith("__door"));
    expect(door!.splatCount, "the door must own the front of the selection").toBeGreaterThan(0);
  });

  it("survives a part that owns no splats at all", async () => {
    // A region that catches nothing is not an error: a selection may simply have no splats
    // in the quadrant a model claimed a part occupies. An empty mesh must still be built, or
    // the whole object fails to bind over one empty part.
    const cloud = room();
    const cam = camera();
    const indices = Uint32Array.from([9000, 9001, 9002, 9003]); // four splats, all in the crate
    const frame = measureFrame(cloud.centers, indices, UP, cam.position);

    const object: PhysicsObject = {
      id: "obj_1",
      label: "thing",
      selectionId: "sel_1",
      massKg: 1,
      friction: 0.6,
      parts: [
        {
          bodyName: "obj_1__shell",
          jointType: "free",
          range: null,
          splatSubset: "all",
          initialPose: {
            bodyName: "obj_1__shell",
            position: [0, 0, 0],
            orientation: [1, 0, 0, 0],
          },
        },
        {
          bodyName: "obj_1__ghost",
          jointType: "hinge",
          range: [0, 90],
          splatSubset: "top_third",
          initialPose: {
            bodyName: "obj_1__ghost",
            position: [0, 0, 0],
            orientation: [1, 0, 0, 0],
          },
        },
      ],
    };

    const staticMesh = new SplatMesh({ packedSplats: cloud.packed, editable: true });
    await staticMesh.initialized;

    const bound = bind(cloud, indices, frame, object);
    expect(bound.parts).toHaveLength(2);
    expect(bound.parts.reduce((n, p) => n + p.splatCount, 0)).toBe(indices.length);
  });
});
