/**
 * Selection: the measurement, and the depth filter that makes it mean anything.
 *
 * A5's acceptance criterion is that a cloud of known dimensions comes back with the right
 * half-extents and a right-handed frame. A real scan can only be judged by eye, so the
 * numbers are checked against a synthetic object whose every property was chosen — a wrong
 * answer there is unambiguous rather than merely suspicious.
 *
 * The depth test is the one that matters most in practice. A rectangle drawn over an object
 * is a frustum, and a frustum does not stop at the object: it continues through the wall
 * behind it. Selecting the wall too does not look like a bug — it looks like a dishwasher
 * that weighs four hundred kilos and is three metres deep.
 */

import * as THREE from "three";
import { describe, expect, it } from "vitest";

import { axesOf, determinant, measureFrame, toSelection } from "./frame";
import { pick, rectFromPointers } from "./pick";
import { REGION_TESTS } from "../types/protocol";

const UP = new THREE.Vector3(0, 0, 1);

/** A filled box of splats with exactly known dimensions, centred where asked. */
function boxCloud(
  centre: [number, number, number],
  size: [number, number, number],
  n = 4000,
  opacity = 0.9,
  seed = 7,
) {
  const centers = new Float32Array(n * 3);
  const opacities = new Float32Array(n);
  let state = seed;
  const rand = () => {
    state = (state * 1664525 + 1013904223) % 4294967296;
    return state / 4294967296;
  };
  for (let i = 0; i < n; i++) {
    centers[i * 3] = centre[0] + (rand() - 0.5) * size[0];
    centers[i * 3 + 1] = centre[1] + (rand() - 0.5) * size[1];
    centers[i * 3 + 2] = centre[2] + (rand() - 0.5) * size[2];
    opacities[i] = opacity;
  }
  return { centers, opacities, count: n };
}

function merge(...clouds: ReturnType<typeof boxCloud>[]) {
  const count = clouds.reduce((s, c) => s + c.count, 0);
  const centers = new Float32Array(count * 3);
  const opacities = new Float32Array(count);
  let at = 0;
  for (const c of clouds) {
    centers.set(c.centers, at * 3);
    opacities.set(c.opacities, at);
    at += c.count;
  }
  return { centers, opacities, count };
}

function allIndices(count: number) {
  return Uint32Array.from({ length: count }, (_, i) => i);
}

/** A camera at `from`, looking at `at`. */
function cameraAt(from: [number, number, number], at: [number, number, number] = [0, 0, 0]) {
  const camera = new THREE.PerspectiveCamera(55, 16 / 9, 0.05, 500);
  camera.up.copy(UP);
  camera.position.set(...from);
  camera.lookAt(...at);
  camera.updateMatrixWorld();
  camera.updateProjectionMatrix();
  return camera;
}

/** Where the viewer is standing. Not the view axis: see measureFrame. */
function eye(camera: THREE.Camera) {
  return camera.position.clone();
}

// ---- measurement -------------------------------------------------------------------------

describe("measuring a selection", () => {
  const cloud = boxCloud([1, 2, 3], [0.6, 0.4, 0.8], 20000);
  // Square on to the box's long horizontal axis, so the expected extents are unambiguous.
  const camera = cameraAt([9, 2, 3], [1, 2, 3]);

  it("recovers the half-extents of a box of known size", () => {
    // A5's acceptance criterion, on data whose dimensions were chosen rather than measured.
    // Sampling a box uniformly does not quite reach its corners, so a few per cent under is
    // correct; anything near half or double would be the classic half-extent confusion.
    const frame = measureFrame(cloud.centers, allIndices(cloud.count), UP, eye(camera));
    expect(frame.halfExtents.z).toBeCloseTo(0.4, 1);
    const horizontal = [frame.halfExtents.x, frame.halfExtents.y].sort((a, b) => b - a);
    expect(horizontal[0]).toBeCloseTo(0.3, 1);
    expect(horizontal[1]).toBeCloseTo(0.2, 1);
  });

  it("puts the centroid at the centre", () => {
    const frame = measureFrame(cloud.centers, allIndices(cloud.count), UP, eye(camera));
    expect(frame.centroid.toArray()).toEqual([
      expect.closeTo(1, 1),
      expect.closeTo(2, 1),
      expect.closeTo(3, 1),
    ]);
  });

  it("always produces a right-handed frame", () => {
    // Eigenvectors come back with arbitrary sign, and a left-handed set is a reflection, not
    // a rotation: it mirrors the object and a door hinges on the wrong edge. Deriving left as
    // up x front makes that impossible rather than something to detect afterwards.
    for (const from of [
      [8, 0, 3],
      [-8, 0, 3],
      [0, 8, 3],
      [0, -8, -3],
      [5, 5, 5],
      [0.01, 0.01, 9],
    ] as const) {
      const camera = cameraAt([...from]);
      const frame = measureFrame(cloud.centers, allIndices(cloud.count), UP, eye(camera));
      expect(determinant(axesOf(frame))).toBeCloseTo(1, 6);
    }
  });

  it("produces orthonormal axes", () => {
    const frame = measureFrame(cloud.centers, allIndices(cloud.count), UP, eye(camera));
    expect(frame.front.length()).toBeCloseTo(1, 6);
    expect(frame.left.length()).toBeCloseTo(1, 6);
    expect(frame.up.length()).toBeCloseTo(1, 6);
    expect(frame.front.dot(frame.left)).toBeCloseTo(0, 6);
    expect(frame.front.dot(frame.up)).toBeCloseTo(0, 6);
    expect(frame.left.dot(frame.up)).toBeCloseTo(0, 6);
  });

  it("points +front at the face the viewer could see", () => {
    // front is a CHOICE among the object's four horizontal faces, not a direction of its
    // own, so the test is that the chosen face is the one turned toward the camera -- within
    // 45 degrees by construction, since one of four axes always is.
    for (const from of [
      [9, 2, 3],
      [-9, 2, 3],
      [1, 10, 3],
      [1, -10, 3],
      [7, 8, 5],
    ] as const) {
      const camera = cameraAt([...from], [1, 2, 3]);
      const frame = measureFrame(cloud.centers, allIndices(cloud.count), UP, eye(camera));
      const toCamera = camera.position.clone().sub(frame.centroid).projectOnPlane(UP).normalize();
      expect(frame.front.dot(toCamera)).toBeGreaterThan(Math.cos(Math.PI / 4) - 1e-6);
    }
  });

  it("measures the same box whatever angle it is viewed from", () => {
    // The reason front is a choice rather than a direction. Taken from the view direction,
    // a 600 mm box seen at 45 degrees measures 850 mm, and the half-extents describe the
    // viewpoint instead of the object.
    const sizes = ([[9, 2, 3], [7, 8, 5], [1, 10, 4], [-6, -5, 3]] as const).map((from) => {
      const frame = measureFrame(
        cloud.centers,
        allIndices(cloud.count),
        UP,
        eye(cameraAt([...from], [1, 2, 3])),
      );
      return [frame.halfExtents.x, frame.halfExtents.y].sort((a, b) => b - a);
    });
    for (const [long, short] of sizes) {
      expect(long).toBeCloseTo(sizes[0][0], 2);
      expect(short).toBeCloseTo(sizes[0][1], 2);
    }
  });

  it("keeps +up as the scene up, not whichever axis was longest", () => {
    const frame = measureFrame(cloud.centers, allIndices(cloud.count), UP, eye(camera));
    expect(frame.up.dot(UP)).toBeCloseTo(1, 6);
  });

  it("survives a camera looking straight down", () => {
    // The projection of the view direction onto the ground plane is zero here, so "front"
    // has to come from the object instead of the camera.
    // Directly above the selection's centroid, so the horizontal component vanishes.
    const overhead = cameraAt([1, 2, 20]);
    const frame = measureFrame(cloud.centers, allIndices(cloud.count), UP, eye(overhead));
    expect(determinant(axesOf(frame))).toBeCloseTo(1, 6);
    expect(Number.isFinite(frame.halfExtents.x)).toBe(true);
  });

  it("never returns a zero half-extent, however flat the selection", () => {
    // A degenerate box is something MuJoCo accepts and then behaves strangely around.
    const flat = boxCloud([0, 0, 0], [1, 1, 0], 500);
    const frame = measureFrame(flat.centers, allIndices(flat.count), UP, eye(camera));
    expect(frame.halfExtents.z).toBeGreaterThan(0);
  });

  it("handles an empty selection without producing NaN", () => {
    const frame = measureFrame(cloud.centers, [], UP, eye(camera));
    expect(Number.isNaN(frame.centroid.x)).toBe(false);
    expect(determinant(axesOf(frame))).toBeCloseTo(1, 6);
  });
});

describe("the wire message", () => {
  it("carries half-extents and a right-handed frame", () => {
    const cloud = boxCloud([0, 0, 0], [0.6, 0.6, 0.85], 8000);
    const camera = cameraAt([6, 0, 1]);
    const frame = measureFrame(cloud.centers, allIndices(cloud.count), UP, eye(camera));
    const selection = toSelection("sel_01", frame, cloud.count);

    expect(selection.splatCount).toBe(cloud.count);
    expect(determinant(selection.axes)).toBeCloseTo(1, 6);
    expect(selection.halfExtents[2]).toBeCloseTo(0.425, 1);
  });
});

// ---- the depth filter --------------------------------------------------------------------

describe("box selection", () => {
  const camera = cameraAt([6, 0, 0]);
  const whole: ReturnType<typeof rectFromPointers> = { x0: -1, y0: -1, x1: 1, y1: 1 };

  it("takes the near object and leaves the wall behind it", () => {
    // The case the whole filter exists for: both are inside the rectangle, because a
    // rectangle is a frustum and a frustum does not stop at the object.
    const object = boxCloud([0, 0, 0], [0.5, 0.5, 0.5], 3000);
    const wall = boxCloud([-4, 0, 0], [0.2, 6, 6], 6000);
    const scene = merge(object, wall);

    const { indices } = pick(scene.centers, scene.opacities, scene.count, camera, whole);

    expect(indices.length).toBeGreaterThan(2000);
    // Every index must come from the first cloud, which occupies [0, 3000).
    expect(Math.max(...indices)).toBeLessThan(object.count);
  });

  it("reports the distance to the surface it locked onto", () => {
    const object = boxCloud([0, 0, 0], [0.5, 0.5, 0.5], 3000);
    const { nearDepth } = pick(object.centers, object.opacities, object.count, camera, whole);
    expect(nearDepth).toBeGreaterThan(5);
    expect(nearDepth).toBeLessThan(6.5);
  });

  it("is not dragged forward by a single floater in front", () => {
    // A minimum would be. A histogram finds the first depth with real mass behind it, which
    // is the surface that was actually pointed at.
    const object = boxCloud([0, 0, 0], [0.5, 0.5, 0.5], 3000);
    const scene = merge(boxCloud([4.5, 0, 0], [0.01, 0.01, 0.01], 2), object);

    const { indices } = pick(scene.centers, scene.opacities, scene.count, camera, whole);
    expect(indices.length).toBeGreaterThan(2000);
  });

  it("ignores splats too faint to see", () => {
    // Only 43% of the playroom capture is above alpha 0.1. Counting the rest reports a
    // number the user cannot reconcile with what is inside their box.
    const solid = boxCloud([0, 0, 0], [0.5, 0.5, 0.5], 2000, 0.9);
    const ghosts = boxCloud([0, 0, 0], [0.5, 0.5, 0.5], 2000, 0.02, 99);
    const scene = merge(solid, ghosts);

    const { indices } = pick(scene.centers, scene.opacities, scene.count, camera, whole);
    expect(indices.length).toBeGreaterThan(1800);
    expect(indices.length).toBeLessThan(2200);
  });

  it("selects nothing when the rectangle is empty space", () => {
    const object = boxCloud([0, 0, 0], [0.5, 0.5, 0.5], 3000);
    const corner = { x0: 0.9, y0: 0.9, x1: 0.99, y1: 0.99 };
    const { indices } = pick(object.centers, object.opacities, object.count, camera, corner);
    expect(indices.length).toBe(0);
  });

  it("ignores what is behind the camera", () => {
    const behind = boxCloud([20, 0, 0], [1, 1, 1], 1000);
    const { indices } = pick(behind.centers, behind.opacities, behind.count, camera, whole);
    expect(indices.length).toBe(0);
  });

  it("a stride gives roughly the same answer for a live count", () => {
    const object = boxCloud([0, 0, 0], [0.5, 0.5, 0.5], 8000);
    const exact = pick(object.centers, object.opacities, object.count, camera, whole);
    const fast = pick(object.centers, object.opacities, object.count, camera, whole, { stride: 8 });
    expect(fast.indices.length * 8).toBeGreaterThan(exact.indices.length * 0.8);
    expect(fast.indices.length * 8).toBeLessThan(exact.indices.length * 1.2);
  });
});

describe("rectFromPointers", () => {
  it("normalises a drag in any direction to the same rectangle", () => {
    const a = rectFromPointers({ x: 100, y: 80 }, { x: 300, y: 200 }, 400, 400);
    const b = rectFromPointers({ x: 300, y: 200 }, { x: 100, y: 80 }, 400, 400);
    expect(a).toEqual(b);
    expect(a.x0).toBeLessThan(a.x1);
    expect(a.y0).toBeLessThan(a.y1);
  });

  it("flips y, because screen y grows downward and NDC y grows up", () => {
    const rect = rectFromPointers({ x: 0, y: 0 }, { x: 400, y: 200 }, 400, 400);
    expect(rect.y1).toBeCloseTo(1, 6); // the top of the screen
    expect(rect.y0).toBeCloseTo(0, 6);
  });
});

// ---- regions, against a real measured frame -----------------------------------------------

describe("resolving a part's region against a measured frame", () => {
  it("puts a bottom-hinged door's splats in the front lower quadrant", () => {
    const camera = cameraAt([6, 0, 0]);
    const cloud = boxCloud([0, 0, 0], [0.6, 0.6, 0.85], 8000);
    const frame = measureFrame(cloud.centers, allIndices(cloud.count), UP, eye(camera));

    // A point on the front face, below centre: the door of a dishwasher.
    const p = frame.centroid
      .clone()
      .addScaledVector(frame.front, frame.halfExtents.x * 0.95)
      .addScaledVector(frame.up, -frame.halfExtents.z * 0.6);

    const d = p.clone().sub(frame.centroid);
    const u = d.dot(frame.front) / frame.halfExtents.x;
    const v = d.dot(frame.left) / frame.halfExtents.y;
    const w = d.dot(frame.up) / frame.halfExtents.z;

    expect(REGION_TESTS.front_lower(u, v, w)).toBe(true);
    expect(REGION_TESTS.front_upper(u, v, w)).toBe(false);
    expect(REGION_TESTS.back(u, v, w)).toBe(false);
  });
});
