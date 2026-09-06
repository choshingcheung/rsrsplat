/**
 * Selecting with a model's silhouette instead of a rectangle.
 *
 * The rectangle tests live in `selection.test.ts`; these cover the SAM path specifically —
 * the coordinate conversion, and the decision that a mask REPLACES the rectangle rather than
 * narrowing it.
 */

import * as THREE from "three";
import { describe, expect, it } from "vitest";

import { pick } from "./pick";
import { masked, type Mask } from "./sam";

describe("picking with a SAM mask", () => {
  /** A mask that is on inside a pixel rectangle and off outside it. */
  function boxMask(
    width: number,
    height: number,
    x0: number,
    y0: number,
    x1: number,
    y1: number,
  ): Mask {
    const data = new Uint8Array(width * height);
    for (let y = y0; y < y1; y++) {
      for (let x = x0; x < x1; x++) data[y * width + x] = 1;
    }
    return { data, width, height };
  }

  it("maps NDC to pixels with y flipped, or the mask is a mirror of the object", () => {
    // The bug this pins looks like a segmentation failure and is a coordinate one: NDC has
    // +y up, pixels have +y down, and getting it backwards selects the reflection.
    const mask = boxMask(100, 100, 0, 0, 100, 50); // the TOP half, in pixels
    expect(masked(mask, 0, 0.5)).toBe(true); // NDC +y is up, so top half
    expect(masked(mask, 0, -0.5)).toBe(false);
  });

  it("selects the silhouette rather than the rectangle", () => {
    // Two splats a metre apart, both inside the drag rectangle. The mask covers only one.
    const centers = Float32Array.from([-0.4, 0, 0, 0.4, 0, 0]);
    const opacities = Float32Array.from([0.9, 0.9]);
    const camera = new THREE.PerspectiveCamera(60, 1, 0.1, 100);
    camera.position.set(0, 0, 3);
    camera.lookAt(0, 0, 0);
    camera.updateMatrixWorld();

    const wide = { x0: -1, y0: -1, x1: 1, y1: 1 };
    const both = pick(centers, opacities, 2, camera, wide);
    expect(both.indices.length).toBe(2);

    // Left half of the screen only: NDC x < 0, which is pixels x < width / 2.
    const left = boxMask(200, 200, 0, 0, 100, 200);
    const one = pick(centers, opacities, 2, camera, wide, { mask: left });
    expect(one.indices.length).toBe(1);
    expect(one.indices[0]).toBe(0); // the splat at x = -0.4
  });

  it("keeps a splat the mask claims even when the rectangle excluded it", () => {
    // The reason the mask replaces the rectangle instead of narrowing it: SAM reaches past
    // the box it was prompted with, and that overspill is the sleeve the drag clipped.
    const centers = Float32Array.from([0.8, 0, 0]);
    const opacities = Float32Array.from([0.9]);
    const camera = new THREE.PerspectiveCamera(60, 1, 0.1, 100);
    camera.position.set(0, 0, 3);
    camera.lookAt(0, 0, 0);
    camera.updateMatrixWorld();

    const tiny = { x0: -0.05, y0: -0.05, x1: 0.05, y1: 0.05 };
    expect(pick(centers, opacities, 1, camera, tiny).indices.length).toBe(0);

    const everything = boxMask(200, 200, 0, 0, 200, 200);
    expect(pick(centers, opacities, 1, camera, tiny, { mask: everything }).indices.length).toBe(1);
  });
});
