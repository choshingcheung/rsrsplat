/**
 * The seam test, TypeScript half.
 *
 * `service/app/protocol.py` and `./protocol.ts` are maintained by hand, and hand-mirrored
 * files drift. `contract/fixtures/` holds one golden JSON message per type;
 * `service/tests/test_protocol.py` is the Python half of the guard and this is the other.
 * Change the seam on one side without the other and one of the two goes red.
 *
 * Two guards operate here, and the first is the more important:
 *
 * 1. COMPILE TIME. Each fixture is assigned to `Loose<T>` of its interface below. Rename a
 *    field in `protocol.ts` and `npm run typecheck` fails on the fixture that still uses
 *    the old name. This does not run as a test; it runs as `tsc`.
 * 2. RUN TIME. Every declared message type must have a fixture on disk, and the region
 *    predicates must behave the way `SubsetRegion` documents.
 */

import { describe, expect, it } from "vitest";

import bodyDrag from "../../../contract/fixtures/client/body.drag.json";
import jointSet from "../../../contract/fixtures/client/joint.set.json";
import meshAttach from "../../../contract/fixtures/client/mesh.attach.json";
import objectPhysicalize from "../../../contract/fixtures/client/object.physicalize.json";
import objectRemove from "../../../contract/fixtures/client/object.remove.json";
import sceneLoad from "../../../contract/fixtures/client/scene.load.json";
import selectionCommit from "../../../contract/fixtures/client/selection.commit.json";
import simControl from "../../../contract/fixtures/client/sim.control.json";
import objectCreated from "../../../contract/fixtures/server/object.created.json";
import objectFailed from "../../../contract/fixtures/server/object.failed.json";
import poseBatch from "../../../contract/fixtures/server/pose.batch.json";
import sceneReady from "../../../contract/fixtures/server/scene.ready.json";
import simStatus from "../../../contract/fixtures/server/sim.status.json";

import {
  CLIENT_MESSAGE_TYPES,
  REGION_TESTS,
  SERVER_MESSAGE_TYPES,
  type Loose,
  type BodyDrag,
  type JointSet,
  type MeshAttach,
  type ObjectCreated,
  type ObjectFailed,
  type ObjectPhysicalize,
  type ObjectRemove,
  type PoseBatch,
  type SceneLoad,
  type SceneReady,
  type SelectionCommit,
  type SimControl,
  type SimStatus,
  type SubsetRegion,
} from "./protocol";

// --- Guard 1: compile time. These assignments are the test. -----------------------------

const CLIENT_FIXTURES = {
  "scene.load": sceneLoad satisfies Loose<SceneLoad>,
  "selection.commit": selectionCommit satisfies Loose<SelectionCommit>,
  "object.physicalize": objectPhysicalize satisfies Loose<ObjectPhysicalize>,
  "object.remove": objectRemove satisfies Loose<ObjectRemove>,
  "joint.set": jointSet satisfies Loose<JointSet>,
  "body.drag": bodyDrag satisfies Loose<BodyDrag>,
  "sim.control": simControl satisfies Loose<SimControl>,
  "mesh.attach": meshAttach satisfies Loose<MeshAttach>,
};

const SERVER_FIXTURES = {
  "scene.ready": sceneReady satisfies Loose<SceneReady>,
  "object.created": objectCreated satisfies Loose<ObjectCreated>,
  "object.failed": objectFailed satisfies Loose<ObjectFailed>,
  "pose.batch": poseBatch satisfies Loose<PoseBatch>,
  "sim.status": simStatus satisfies Loose<SimStatus>,
};

// --- Guard 2: run time. -----------------------------------------------------------------

describe("the protocol fixtures", () => {
  it("cover every client message type", () => {
    expect(Object.keys(CLIENT_FIXTURES).sort()).toEqual([...CLIENT_MESSAGE_TYPES].sort());
  });

  it("cover every server message type", () => {
    expect(Object.keys(SERVER_FIXTURES).sort()).toEqual([...SERVER_MESSAGE_TYPES].sort());
  });

  it("carry the type tag their filename claims", () => {
    for (const [name, fixture] of Object.entries({ ...CLIENT_FIXTURES, ...SERVER_FIXTURES })) {
      expect(fixture.type).toBe(name);
    }
  });
});

describe("the selection frame", () => {
  it("is right-handed", () => {
    // A left-handed PCA frame is a reflection rather than a rotation and mirrors
    // everything downstream. The determinant is how you find out.
    const a = selectionCommit.selection.axes;
    const det =
      a[0] * (a[4] * a[8] - a[5] * a[7]) -
      a[3] * (a[1] * a[8] - a[2] * a[7]) +
      a[6] * (a[1] * a[5] - a[2] * a[4]);
    expect(det).toBeCloseTo(1, 9);
  });

  it("reports half-extents, not full widths", () => {
    // A 600 x 600 x 850 mm dishwasher. Full widths here would be the classic MJCF
    // box-size mistake showing up one layer earlier.
    expect(selectionCommit.selection.halfExtents).toEqual([0.3, 0.3, 0.425]);
  });
});

describe("wire quaternions", () => {
  it("are unit length and scalar-first", () => {
    for (const pose of poseBatch.poses) {
      const [w, x, y, z] = pose.orientation;
      expect(w * w + x * x + y * y + z * z).toBeCloseTo(1, 6);
    }
    // A 30 degree yaw about scene up: the scalar part is cos(15 degrees). If the wire ever
    // switched to (x, y, z, w) this would land at ~0.259 instead.
    expect(poseBatch.poses[0].orientation[0]).toBeCloseTo(Math.cos((15 * Math.PI) / 180), 9);
  });
});

describe("joint ranges", () => {
  it("are in degrees, not radians", () => {
    const door = objectCreated.object.parts.find((p) => p.jointType === "hinge");
    expect(door?.range).toEqual([0, 90]);
  });
});

describe("REGION_TESTS", () => {
  it("has a predicate for every declared region", () => {
    const declared: SubsetRegion[] = [
      "all",
      "left",
      "right",
      "front",
      "back",
      "top",
      "bottom",
      "front_upper",
      "front_lower",
      "top_third",
      "bottom_third",
      "left_third",
      "right_third",
    ];
    expect(Object.keys(REGION_TESTS).sort()).toEqual([...declared].sort());
  });

  it("partitions opposing half-spaces without overlap", () => {
    for (const [a, b] of [
      ["left", "right"],
      ["front", "back"],
      ["top", "bottom"],
    ] as const) {
      for (const u of [-0.7, 0.7]) {
        for (const v of [-0.7, 0.7]) {
          for (const w of [-0.7, 0.7]) {
            expect(REGION_TESTS[a](u, v, w)).not.toBe(REGION_TESTS[b](u, v, w));
          }
        }
      }
    }
  });

  it("puts a bottom-hinged door panel in front_lower and nowhere above", () => {
    // Where a dishwasher door's splats sit: forward along +front (u), below centre in w.
    expect(REGION_TESTS.front_lower(0.9, 0, -0.6)).toBe(true);
    expect(REGION_TESTS.front_upper(0.9, 0, -0.6)).toBe(false);
    expect(REGION_TESTS.bottom_third(0.9, 0, -0.6)).toBe(true);
    // And it must not be caught by the back half, which would be the frame swapped.
    expect(REGION_TESTS.back(0.9, 0, -0.6)).toBe(false);
  });

  it("selects everything for 'all'", () => {
    expect(REGION_TESTS.all(0, 0, 0)).toBe(true);
    expect(REGION_TESTS.all(-1, 1, -1)).toBe(true);
  });
});

describe("joint.set", () => {
  it("carries a hinge target in degrees, matching the part's own range", () => {
    // The units trap this whole project keeps hitting. qpos is radians; the wire is not.
    // 62.5 is unambiguous -- a radian value inside a 0-90 degree range would be under 1.6.
    expect(jointSet.value).toBe(62.5);
    expect(jointSet.value).toBeGreaterThan(Math.PI / 2);
  });
});

describe("body.drag", () => {
  it("carries a point in the room, and null to release", () => {
    // Null is the release, not a separate message. One verb, two states -- the alternative
    // was body.grab/body.release and a state machine spanning the socket.
    expect(bodyDrag.target).toHaveLength(3);
    const released: Loose<BodyDrag> = { ...bodyDrag, target: null };
    expect(released.target).toBeNull();
  });
});

describe("a selection's measured shape", () => {
  it("carries more than one box, which is the whole point", () => {
    // One box is what halfExtents already says. Two or more is a shape a bounding box cannot
    // express -- and the difference between a chair with legs and a cuboid full of air.
    expect(selectionCommit.selection.shape.length).toBeGreaterThan(1);
  });

  it("is expressed in the selection's own frame", () => {
    // Centres sit around the origin, because the frame's origin IS the object's centroid.
    // World coordinates here would put every box in the wrong place on a rotated object.
    for (const box of selectionCommit.selection.shape) {
      for (const v of box.center) expect(Math.abs(v)).toBeLessThan(1);
      for (const v of box.halfExtents) expect(v).toBeGreaterThan(0);
    }
  });
});
