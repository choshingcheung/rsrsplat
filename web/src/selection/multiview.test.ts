/**
 * Voting across viewpoints, and the box each viewpoint prompts with.
 *
 * `captureOrbit` needs a live WebGL context and is exercised by hand; the two pure pieces
 * carry the logic that decides what survives, so they are tested here.
 */

import * as THREE from "three";
import { describe, expect, it } from "vitest";

import { boundsIn, vote } from "./multiview";

describe("voting across viewpoints", () => {
  it("keeps what most views agree on and drops what one view imagined", () => {
    // Splat 1 is in every view: the object. Splat 7 is in one: a mask that latched onto a
    // shadow. Splat 3 is in two of three, which is the interesting case -- an object edge
    // that goes out of frame at a steep angle should survive.
    const kept = vote([
      Uint32Array.from([1, 3, 7]),
      Uint32Array.from([1, 3]),
      Uint32Array.from([1]),
    ], 10);

    expect([...kept]).toEqual([1, 3]);
  });

  it("does not require unanimity, because one bad mask must not empty the selection", () => {
    // Losing everything is a worse failure than keeping a little carpet: the user sees
    // nothing happen and has no way to tell why.
    const kept = vote([
      Uint32Array.from([1, 2, 3]),
      Uint32Array.from([1, 2, 3]),
      Uint32Array.from([]), // the model failed on this angle
    ], 10);

    expect([...kept]).toEqual([1, 2, 3]);
  });

  it("returns nothing when there were no views at all", () => {
    expect(vote([], 10).length).toBe(0);
  });

  it("tightens with a stricter agreement threshold", () => {
    const views = [
      Uint32Array.from([1, 2]),
      Uint32Array.from([1]),
      Uint32Array.from([1]),
    ];
    expect([...vote(views, 5, 0.3)]).toEqual([1, 2]);
    expect([...vote(views, 5, 1.0)]).toEqual([1]);
  });
});

describe("the prompt box in each view", () => {
  function camera(): THREE.PerspectiveCamera {
    const c = new THREE.PerspectiveCamera(60, 1, 0.1, 100);
    c.position.set(0, 0, 4);
    c.lookAt(0, 0, 0);
    c.updateMatrixWorld();
    return c;
  }

  it("bounds the selection, with padding, inside the screen", () => {
    const centers = Float32Array.from([-0.5, -0.5, 0, 0.5, 0.5, 0]);
    const rect = boundsIn(camera(), centers, [0, 1]);

    expect(rect.x0).toBeLessThan(0);
    expect(rect.x1).toBeGreaterThan(0);
    expect(rect.x0).toBeGreaterThanOrEqual(-1);
    expect(rect.y1).toBeLessThanOrEqual(1);
    expect(rect.x1 - rect.x0).toBeLessThan(2); // it is a box round the object, not the screen
  });

  it("falls back to the whole screen when nothing projects in front of the camera", () => {
    // Everything behind the camera. A degenerate box would prompt the model with an empty
    // region, and an empty prompt is worse than a loose one.
    const centers = Float32Array.from([0, 0, 40]);
    const rect = boundsIn(camera(), centers, [0]);
    expect(rect).toEqual({ x0: -1, y0: -1, x1: 1, y1: 1 });
  });
});
