/**
 * Turning 30 Hz of physics into 60 fps of motion, without lying about the motion.
 *
 * **The first version of this smoothed instead of interpolating**, and that is what made the
 * physics feel wrong. It moved each body 35% of the way toward the newest pose every frame:
 *
 *     mesh.position.lerp(target, 0.35)
 *
 * which is a first-order low-pass filter, not interpolation. Its consequences are exactly
 * what a falling object should not do:
 *
 * - **Acceleration is damped.** A body in freefall is always chasing a target it never
 *   reaches, so gravity reads as weaker than it is — descending through syrup.
 * - **Impacts are smeared.** A crate hitting a floor stops in one step. Exponential
 *   smoothing turns that abrupt stop into a soft arrival over ~10 frames.
 * - **Everything acquires an invisible spring**, uniformly, whatever it is made of.
 *
 * The right answer is standard for networked physics and is not smoothing at all: **buffer
 * the poses, render slightly in the past, and interpolate between the two snapshots that
 * bracket the render time by the true elapsed fraction.** That reproduces the simulated
 * trajectory exactly, delayed by about one update. Sharp landings stay sharp because the two
 * snapshots either side of the landing really are that far apart.
 *
 * The cost is one broadcast period of latency, which at 30 Hz is 33 ms and invisible. The
 * cost of the alternative was making every object feel like it was made of foam.
 */

import * as THREE from "three";

import type { PoseBatch, PoseUpdate } from "../types/protocol";
import { toThree } from "./quaternion";

/** How far behind the newest snapshot to render, in SECONDS of simulated time. */
const DELAY = 1 / 30;

/** Snapshots kept. Enough to interpolate across a dropped update, not enough to drift. */
const HISTORY = 6;

/**
 * If the render clock falls further behind or ahead than this, jump rather than crawl.
 *
 * Happens when the tab is backgrounded, the socket stalls, or the simulation is reset. Easing
 * back over hundreds of frames would look like everything slowly sliding into place.
 */
const RESYNC_SECONDS = 0.5;

interface Snapshot {
  t: number;
  poses: Map<string, PoseUpdate>;
}

export class PoseStream {
  private snapshots: Snapshot[] = [];
  private renderTime = 0;
  private started = false;

  /** Take a batch off the wire. */
  push(batch: PoseBatch): void {
    // A reset rewinds simulated time, so the history before it describes another world.
    if (this.snapshots.length && batch.t < this.snapshots[this.snapshots.length - 1].t) {
      this.snapshots = [];
      this.started = false;
    }

    this.snapshots.push({
      t: batch.t,
      poses: new Map(batch.poses.map((pose) => [pose.bodyName, pose])),
    });
    if (this.snapshots.length > HISTORY) this.snapshots.shift();

    if (!this.started) {
      // The FIRST snapshot's own time, not one delay before it. Starting a delay earlier
      // leaves the clock permanently two periods behind the newest rather than one: it only
      // ever advances at the rate updates arrive, so it never makes the difference up.
      this.renderTime = batch.t;
      this.started = true;
    }
  }

  /**
   * Advance the render clock by a real frame's worth of time.
   *
   * Driven by the render loop's own delta rather than by arrivals, so motion stays smooth
   * between updates instead of stepping on each one.
   */
  advance(dt: number): void {
    if (!this.started) return;
    this.renderTime += dt;

    const newest = this.snapshots[this.snapshots.length - 1]?.t;
    if (newest === undefined) return;

    const oldest = this.snapshots[0].t;
    const target = newest - DELAY;

    // Behind the buffer entirely: the snapshots we would have interpolated across have
    // already been dropped, so there is nothing to crawl back through. Happens after a
    // stall, and after a burst of updates arrives faster than frames are drawn.
    if (this.renderTime < oldest || Math.abs(this.renderTime - target) > RESYNC_SECONDS) {
      this.renderTime = Math.max(oldest, target);
      return;
    }
    // Never run past the newest snapshot: extrapolating without a velocity on the wire
    // invents motion, and invented motion is worse than a held frame.
    if (this.renderTime > newest) this.renderTime = newest;
  }

  /** Where a body is at the current render time, or null if it is not being streamed. */
  sample(bodyName: string, position: THREE.Vector3, rotation: THREE.Quaternion): boolean {
    if (this.snapshots.length === 0) return false;

    if (this.snapshots.length === 1) {
      const only = this.snapshots[0].poses.get(bodyName);
      if (!only) return false;
      position.set(only.position[0], only.position[1], only.position[2]);
      toThree(only.orientation, rotation);
      return true;
    }

    // The pair bracketing the render time.
    let before = this.snapshots[0];
    let after = this.snapshots[this.snapshots.length - 1];
    for (let i = 0; i < this.snapshots.length - 1; i++) {
      if (
        this.snapshots[i].t <= this.renderTime &&
        this.snapshots[i + 1].t >= this.renderTime
      ) {
        before = this.snapshots[i];
        after = this.snapshots[i + 1];
        break;
      }
    }

    const a = before.poses.get(bodyName);
    const b = after.poses.get(bodyName);
    if (!a && !b) return false;
    if (!a || !b) {
      const only = (a ?? b)!;
      position.set(only.position[0], only.position[1], only.position[2]);
      toThree(only.orientation, rotation);
      return true;
    }

    const span = after.t - before.t;
    // The true elapsed fraction, not a fixed rate. This is the whole difference.
    const alpha = span > 1e-9 ? Math.min(1, Math.max(0, (this.renderTime - before.t) / span)) : 1;

    position.set(a.position[0], a.position[1], a.position[2]);
    SCRATCH.set(b.position[0], b.position[1], b.position[2]);
    position.lerp(SCRATCH, alpha);

    toThree(a.orientation, rotation);
    rotation.slerp(toThree(b.orientation, SCRATCH_Q), alpha);
    return true;
  }

  /** Stop streaming a body, so a removed object leaves no stale snapshot behind. */
  forget(bodyName: string): void {
    for (const snapshot of this.snapshots) snapshot.poses.delete(bodyName);
  }

  clear(): void {
    this.snapshots = [];
    this.started = false;
  }

  get bodyCount(): number {
    return this.snapshots[this.snapshots.length - 1]?.poses.size ?? 0;
  }
}

const SCRATCH = new THREE.Vector3();
const SCRATCH_Q = new THREE.Quaternion();

export { DELAY, RESYNC_SECONDS };
