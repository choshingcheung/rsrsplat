/**
 * The mock is the demo's insurance policy, so it gets tested like one.
 *
 * A4's proof is that the whole interface works with no Python process running. That means
 * the mock has to emit every message the real service does, in the same shapes, and produce
 * a trajectory that actually falls and actually stops — a body that jitters against the
 * floor forever reads as a physics bug even in a stand-in.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { MockService } from "./mock";
import type { PoseBatch, ServerMessage, Selection, WorldFrame } from "../types/protocol";

const GROUND = -1.42;

const WORLD: WorldFrame = { up: [0, 0, 1], groundHeight: GROUND, sceneScale: 1 };

function selection(
  id = "sel_01",
  centroid: [number, number, number] = [0, 0, GROUND + 1],
  halfExtents: [number, number, number] = [0.25, 0.25, 0.2],
): Selection {
  return {
    id,
    splatCount: 1000,
    centroid,
    axes: [1, 0, 0, 0, 1, 0, 0, 0, 1],
    halfExtents,
  };
}

function harness() {
  const messages: ServerMessage[] = [];
  const service = new MockService({ onMessage: (m) => messages.push(m) });
  const of = <T extends ServerMessage["type"]>(type: T) =>
    messages.filter((m) => m.type === type) as Extract<ServerMessage, { type: T }>[];
  return { service, messages, of };
}

beforeEach(() => vi.useFakeTimers());
afterEach(() => vi.useRealTimers());

describe("the handshake", () => {
  it("acknowledges a scene before anything else", () => {
    const { service, messages } = harness();
    service.send({ type: "scene.load", splatId: "x", splatCount: 10, world: WORLD, obstacles: [] });

    expect(messages[0].type).toBe("scene.ready");
    expect(messages[1].type).toBe("sim.status");
    service.stop();
  });

  it("sends nothing at all until something is physicalized", () => {
    const { service, of } = harness();
    service.send({ type: "scene.load", splatId: "x", splatCount: 10, world: WORLD, obstacles: [] });
    vi.advanceTimersByTime(500);

    expect(of("pose.batch")).toHaveLength(0);
    service.stop();
  });
});

describe("physicalize", () => {
  it("refuses a selection that was never committed, the way the service does", () => {
    const { service, of } = harness();
    service.send({ type: "scene.load", splatId: "x", splatCount: 10, world: WORLD, obstacles: [] });
    service.send({ type: "object.physicalize", selectionId: "ghost", prompt: "a crate" });

    expect(of("object.failed")[0].reason).toContain("unknown selection");
    service.stop();
  });

  it("makes a crate a free body with a mass that follows the words", () => {
    const { service, of } = harness();
    service.send({ type: "scene.load", splatId: "x", splatCount: 10, world: WORLD, obstacles: [] });
    service.send({ type: "selection.commit", selection: selection() });
    service.send({ type: "object.physicalize", selectionId: "sel_01", prompt: "a heavy crate" });

    const created = of("object.created")[0].object;
    expect(created.parts[0].jointType).toBe("free");
    expect(created.parts[0].splatSubset).toBe("all");
    expect(created.massKg).toBeGreaterThan(0);
    service.stop();
  });

  it("weighs a heavy thing more than a light one", () => {
    const heavy = harness();
    const light = harness();
    for (const [h, prompt] of [
      [heavy, "a solid steel crate"],
      [light, "an empty cardboard box"],
    ] as const) {
      h.service.send({ type: "scene.load", splatId: "x", splatCount: 10, world: WORLD, obstacles: [] });
      h.service.send({ type: "selection.commit", selection: selection() });
      h.service.send({ type: "object.physicalize", selectionId: "sel_01", prompt });
      h.service.stop();
    }
    expect(heavy.of("object.created")[0].object.massKg).toBeGreaterThan(
      4 * light.of("object.created")[0].object.massKg,
    );
  });

  it("gives a dishwasher a hinge, in degrees, owning a subset", () => {
    const { service, of } = harness();
    service.send({ type: "scene.load", splatId: "x", splatCount: 10, world: WORLD, obstacles: [] });
    service.send({ type: "selection.commit", selection: selection("sel_01", [0, 0, 0], [0.3, 0.3, 0.425]) });
    service.send({
      type: "object.physicalize",
      selectionId: "sel_01",
      prompt: "a dishwasher, the door hinges at the bottom",
    });

    const created = of("object.created")[0].object;
    const door = created.parts.find((p) => p.jointType === "hinge");
    expect(door?.range).toEqual([0, 90]);
    expect(door?.splatSubset).not.toBe("all");
    expect(created.parts[0].jointType).toBe("fixed"); // an appliance does not tumble
    service.stop();
  });

  it("places the hinge on the bottom front edge, as the generator does", () => {
    // centroid + halfDepth * front - halfHeight * up, with an identity frame.
    const { service, of } = harness();
    service.send({ type: "scene.load", splatId: "x", splatCount: 10, world: WORLD, obstacles: [] });
    service.send({ type: "selection.commit", selection: selection("sel_01", [1, 2, 3], [0.3, 0.3, 0.4]) });
    service.send({ type: "object.physicalize", selectionId: "sel_01", prompt: "a dishwasher" });

    const door = of("object.created")[0].object.parts.find((p) => p.jointType === "hinge")!;
    expect(door.initialPose.position).toEqual([1.3, 2, 2.6]);
    service.stop();
  });

  it("carries a unit quaternion on every part", () => {
    const { service, of } = harness();
    service.send({ type: "scene.load", splatId: "x", splatCount: 10, world: WORLD, obstacles: [] });
    service.send({ type: "selection.commit", selection: selection() });
    service.send({ type: "object.physicalize", selectionId: "sel_01", prompt: "a dishwasher" });

    for (const part of of("object.created")[0].object.parts) {
      const [w, x, y, z] = part.initialPose.orientation;
      expect(w * w + x * x + y * y + z * z).toBeCloseTo(1, 6);
    }
    service.stop();
  });
});

describe("the stream", () => {
  function fall(prompt = "a crate") {
    const { service, of } = harness();
    service.send({ type: "scene.load", splatId: "x", splatCount: 10, world: WORLD, obstacles: [] });
    service.send({ type: "selection.commit", selection: selection() });
    service.send({ type: "object.physicalize", selectionId: "sel_01", prompt });
    vi.advanceTimersByTime(3000);
    service.stop();
    return of("pose.batch") as PoseBatch[];
  }

  it("delivers batches with advancing simulated time", () => {
    const batches = fall();
    expect(batches.length).toBeGreaterThan(50);
    expect(batches.at(-1)!.t).toBeGreaterThan(batches[0].t);
  });

  it("drops a free body and brings it to rest ON the floor", () => {
    const heights = fall().map((b) => b.poses[0].position[2]);

    expect(heights[0]).toBeGreaterThan(GROUND + 0.5);
    expect(Math.min(...heights)).toBeGreaterThanOrEqual(GROUND);
    // 0.2 is the selection's half-height: it rests on its base, not its centre.
    expect(heights.at(-1)).toBeCloseTo(GROUND + 0.2, 2);
  });

  it("stops rather than jittering against the floor forever", () => {
    const heights = fall().map((b) => b.poses[0].position[2]);
    const tail = heights.slice(-15);
    expect(Math.max(...tail) - Math.min(...tail)).toBeLessThan(1e-6);
  });

  it("swings a door open and holds it there", () => {
    const batches = fall("a dishwasher");
    const door = (b: PoseBatch) => b.poses.find((p) => p.bodyName.endsWith("__door"))!;

    const first = door(batches[0]).orientation;
    const last = door(batches.at(-1)!).orientation;
    expect(last).not.toEqual(first);

    // Held, not still swinging.
    const settled = door(batches.at(-8)!).orientation;
    for (let i = 0; i < 4; i++) expect(last[i]).toBeCloseTo(settled[i], 4);
  });
});

describe("control", () => {
  function running() {
    const h = harness();
    h.service.send({ type: "scene.load", splatId: "x", splatCount: 10, world: WORLD, obstacles: [] });
    h.service.send({ type: "selection.commit", selection: selection() });
    h.service.send({ type: "object.physicalize", selectionId: "sel_01", prompt: "a crate" });
    return h;
  }

  it("pause freezes the scene and play resumes it", () => {
    const { service, of } = running();
    vi.advanceTimersByTime(200);
    service.send({ type: "sim.control", action: "pause" });
    const atPause = of("pose.batch").length;

    vi.advanceTimersByTime(500);
    expect(of("pose.batch")).toHaveLength(atPause);

    service.send({ type: "sim.control", action: "play" });
    vi.advanceTimersByTime(200);
    expect(of("pose.batch").length).toBeGreaterThan(atPause);
    service.stop();
  });

  it("reset returns everything to where it started", () => {
    const { service, of } = running();
    const start = of("object.created")[0].object.parts[0].initialPose.position[2];
    vi.advanceTimersByTime(600);
    expect(of("pose.batch").at(-1)!.poses[0].position[2]).toBeLessThan(start);

    service.send({ type: "sim.control", action: "reset" });
    vi.advanceTimersByTime(40);
    expect(of("pose.batch").at(-1)!.poses[0].position[2]).toBeCloseTo(start, 1);
    service.stop();
  });

  it("removing an object takes it out of the stream", () => {
    const { service, of } = running();
    const id = of("object.created")[0].object.id;
    vi.advanceTimersByTime(100);

    service.send({ type: "object.remove", objectId: id });
    const after = of("pose.batch").length;
    vi.advanceTimersByTime(300);

    expect(of("pose.batch")).toHaveLength(after);
    service.stop();
  });
});
