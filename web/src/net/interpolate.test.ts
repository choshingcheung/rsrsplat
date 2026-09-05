/**
 * Interpolating physics, and the difference between that and smoothing it.
 *
 * The test that matters is `reproduces a falling trajectory`. The previous implementation
 * eased toward the newest pose by a fixed fraction each frame, which is a low-pass filter:
 * it damped acceleration and smeared impacts, and made a falling crate look like it was
 * descending through syrup. These assertions are what tell the two apart.
 */

import * as THREE from "three";
import { describe, expect, it } from "vitest";

import { PoseStream } from "./interpolate";
import type { PoseBatch, PoseUpdate } from "../types/protocol";

const RATE = 1 / 30;

function pose(bodyName: string, z: number, yaw = 0): PoseUpdate {
  return {
    bodyName,
    position: [0, 0, z],
    orientation: [Math.cos(yaw / 2), 0, 0, Math.sin(yaw / 2)],
  };
}

function batch(t: number, poses: PoseUpdate[]): PoseBatch {
  return { type: "pose.batch", t, poses };
}

/** Height of a body dropped from `z0`, under gravity, at time `t`. */
function freefall(t: number, z0 = 5): number {
  return z0 - 0.5 * 9.81 * t * t;
}

function sample(stream: PoseStream, body = "crate"): THREE.Vector3 | null {
  const position = new THREE.Vector3();
  const rotation = new THREE.Quaternion();
  return stream.sample(body, position, rotation) ? position.clone() : null;
}

describe("a stream with nothing in it", () => {
  it("reports no sample rather than a zero", () => {
    // A body at the origin and a body with no data must not look the same.
    expect(sample(new PoseStream())).toBeNull();
  });

  it("holds the only pose it has", () => {
    const stream = new PoseStream();
    stream.push(batch(0, [pose("crate", 5)]));
    stream.advance(1);
    expect(sample(stream)!.z).toBe(5);
  });
});

describe("interpolating", () => {
  it("lands exactly on a snapshot at that snapshot's time", () => {
    const stream = new PoseStream();
    stream.push(batch(0, [pose("crate", 0)]));
    stream.push(batch(RATE, [pose("crate", 10)]));
    // The clock sits on the first snapshot, so this is exactly it -- not near it.
    expect(sample(stream)!.z).toBeCloseTo(0, 9);
  });

  it("moves by the ELAPSED FRACTION between two snapshots, not a fixed rate", () => {
    // The whole difference from smoothing. Half a period elapsed is half the distance,
    // whatever the frame rate happens to be.
    const stream = new PoseStream();
    stream.push(batch(0, [pose("crate", 0)]));
    stream.push(batch(RATE, [pose("crate", 10)]));
    stream.advance(RATE / 2);
    expect(sample(stream)!.z).toBeCloseTo(5, 6);
  });

  it("gives the same answer whether it got there in one frame or ten", () => {
    // A smoothing filter cannot do this: its output depends on how many times it ran.
    const coarse = new PoseStream();
    const fine = new PoseStream();
    for (const stream of [coarse, fine]) {
      stream.push(batch(0, [pose("crate", 0)]));
      stream.push(batch(RATE, [pose("crate", 10)]));
    }
    coarse.advance(RATE / 2);
    for (let i = 0; i < 10; i++) fine.advance(RATE / 20);

    expect(sample(coarse)!.z).toBeCloseTo(sample(fine)!.z, 6);
  });

  it("reproduces a falling trajectory instead of damping it", () => {
    // Real freefall arriving at 30 Hz while frames are drawn at 60, which is the actual
    // situation. Compare each sampled height against the true parabola at the same instant.
    //
    // Linear interpolation across a 33 ms chord of a parabola is accurate to about a
    // millimetre at these speeds. Exponential smoothing lagged by tens of centimetres and
    // never caught up, because a body in freefall is always accelerating away from it.
    const stream = new PoseStream();
    let worst = 0;
    let clock = 0;

    for (let i = 0; i <= 12; i++) {
      const newest = i * RATE;
      stream.push(batch(newest, [pose("crate", freefall(newest))]));
      for (let frame = 0; frame < 2; frame++) {
        stream.advance(RATE / 2);
        // Mirror the stream's own clamp: it never runs past the newest snapshot.
        clock = Math.min(clock + RATE / 2, newest);
        worst = Math.max(worst, Math.abs(sample(stream)!.z - freefall(clock)));
      }
    }
    expect(worst).toBeLessThan(0.01);
  });

  it("keeps an impact abrupt", () => {
    // A crate hitting a floor stops within one step. Smoothing turned that into a soft
    // arrival over ten frames, which is the single most obviously wrong thing it did.
    const stream = new PoseStream();
    stream.push(batch(0, [pose("crate", 1.0)]));
    stream.push(batch(RATE, [pose("crate", 0.5)]));
    stream.push(batch(2 * RATE, [pose("crate", 0.0)]));
    stream.push(batch(3 * RATE, [pose("crate", 0.0)]));
    stream.push(batch(4 * RATE, [pose("crate", 0.0)]));

    // Advance well past the landing.
    for (let i = 0; i < 8; i++) stream.advance(RATE / 2);
    expect(sample(stream)!.z).toBe(0);
  });
});

describe("orientation", () => {
  it("slerps between snapshots", () => {
    const stream = new PoseStream();
    stream.push(batch(0, [pose("crate", 0, 0)]));
    stream.push(batch(RATE, [pose("crate", 0, Math.PI / 2)]));
    stream.advance(RATE / 2);

    const position = new THREE.Vector3();
    const rotation = new THREE.Quaternion();
    stream.sample("crate", position, rotation);

    const expected = new THREE.Quaternion().setFromAxisAngle(
      new THREE.Vector3(0, 0, 1),
      Math.PI / 4,
    );
    expect(rotation.angleTo(expected)).toBeCloseTo(0, 6);
  });
});

describe("robustness", () => {
  it("never extrapolates past the newest snapshot", () => {
    // The wire carries no velocity, so extrapolating would invent motion. A held frame is
    // better than a body that drifts off on its own when the socket stalls.
    const stream = new PoseStream();
    stream.push(batch(0, [pose("crate", 0)]));
    stream.push(batch(RATE, [pose("crate", 10)]));
    for (let i = 0; i < 100; i++) stream.advance(RATE);
    expect(sample(stream)!.z).toBe(10);
  });

  it("resynchronises after a long stall rather than crawling back", () => {
    // A backgrounded tab. Easing back over hundreds of frames would look like the whole
    // scene sliding into place.
    const stream = new PoseStream();
    stream.push(batch(0, [pose("crate", 0)]));
    stream.advance(30);
    stream.push(batch(60, [pose("crate", 42)]));
    stream.push(batch(60 + RATE, [pose("crate", 42)]));
    stream.advance(1 / 60);
    expect(sample(stream)!.z).toBeCloseTo(42, 3);
  });

  it("starts over when simulated time rewinds", () => {
    // What a reset looks like from here: history before it describes a different world.
    const stream = new PoseStream();
    stream.push(batch(5, [pose("crate", 0)]));
    stream.push(batch(5 + RATE, [pose("crate", 1)]));
    stream.push(batch(0, [pose("crate", 9)]));
    stream.advance(RATE);
    expect(sample(stream)!.z).toBe(9);
  });

  it("interpolates across a dropped update", () => {
    const stream = new PoseStream();
    stream.push(batch(0, [pose("crate", 0)]));
    stream.push(batch(2 * RATE, [pose("crate", 20)])); // one batch lost in transit
    stream.advance(RATE); // halfway across the gap the lost batch would have filled
    expect(sample(stream)!.z).toBeCloseTo(10, 6);
  });

  it("forgets a removed body", () => {
    const stream = new PoseStream();
    stream.push(batch(0, [pose("crate", 0), pose("door", 1)]));
    stream.push(batch(RATE, [pose("crate", 0), pose("door", 1)]));
    stream.forget("door");

    expect(sample(stream, "crate")).not.toBeNull();
    expect(sample(stream, "door")).toBeNull();
  });

  it("handles a body appearing mid-stream", () => {
    // Physicalising a second object: it exists in the newer snapshot and not the older.
    const stream = new PoseStream();
    stream.push(batch(0, [pose("crate", 0)]));
    stream.push(batch(RATE, [pose("crate", 0), pose("bottle", 7)]));
    stream.advance(RATE / 2);
    expect(sample(stream, "bottle")!.z).toBe(7);
  });
});
