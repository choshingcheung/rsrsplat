/**
 * Turning a drag rectangle into an object mask, with SAM 2.
 *
 * A rectangle plus a depth filter selects a slab of the frustum: the vest, the carpet around
 * it, and whatever is behind. Growing that geometrically does not recover — measured on the
 * real capture it returned 801% of the selection, because a room scan is one connected mass
 * and "connected to" therefore means "in the same room as". Geometry cannot say where an
 * object ends; that needs an image prior.
 *
 * The browser already projects every splat to the screen in `pick`, so a mask is a drop-in
 * replacement for the rectangle test: a splat belongs to the object if it lands inside the
 * silhouette AND at the right depth. Depth still matters — the wall behind the vest is also
 * inside the vest's silhouette.
 *
 * The model runs in a local sidecar (`sam/server.py`), and every failure path here returns
 * null so the caller falls back to the plain rectangle. A demo that quietly degrades is worth
 * more than one that throws when a sidecar is not running.
 */

import type { ScreenRect } from "./pick";

/** A binary mask over the viewport, in DEVICE pixels, row 0 at the top. */
export interface Mask {
  data: Uint8Array;
  width: number;
  height: number;
}

const ENDPOINT = import.meta.env.VITE_SAM_URL ?? "http://127.0.0.1:8008";

/** Longest edge sent to the model. SAM works at ~1024; sending 4K just costs upload time. */
const MAX_EDGE = 1280;

let available: boolean | null = null;

/** Is the sidecar up? Cached, so a missing sidecar costs one request per session. */
export async function samAvailable(timeoutMs = 800): Promise<boolean> {
  if (available !== null) return available;
  try {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    const response = await fetch(`${ENDPOINT}/health`, { signal: controller.signal });
    clearTimeout(timer);
    available = response.ok;
  } catch {
    available = false;
  }
  return available;
}

/**
 * The rendered view, frozen at the moment the drag ended.
 *
 * Captured then rather than when it is used, because segmentation happens after the user has
 * typed a sentence and they may well have orbited in between. A mask computed from a
 * different camera than the rectangle was drawn in is nonsense, and looks like the model
 * being wrong.
 */
export interface View {
  blob: Blob;
  /** Device pixels of the ORIGINAL canvas, which is the space masks come back in. */
  width: number;
  height: number;
  /** How much the blob was downscaled by, so box coordinates can follow. */
  scale: number;
}

/** Freeze the current view. Needs `preserveDrawingBuffer` on the renderer. */
export async function captureView(canvas: HTMLCanvasElement): Promise<View | null> {
  const width = canvas.width;
  const height = canvas.height;
  if (width === 0 || height === 0) return null;
  const scale = Math.min(1, MAX_EDGE / Math.max(width, height));
  const blob = await capture(canvas, scale);
  return blob ? { blob, width, height, scale } : null;
}

/**
 * Ask the model what object is in this rectangle, optionally told what to look for.
 *
 * The rectangle and the sentence answer different halves of the question and the sidecar
 * uses both: the sentence says WHAT, the rectangle says WHICH ONE. A room may hold several
 * vests and the most confident one is not necessarily the one being pointed at.
 *
 * Returns null on any failure, including the sidecar being absent, so the caller falls back
 * to the plain rectangle.
 */
export async function segmentView(
  view: View,
  rect: ScreenRect,
  prompt = "",
  timeoutMs = 15000,
): Promise<Mask | null> {
  if (!(await samAvailable())) return null;

  const { width, height, scale } = view;

  // NDC is -1..1 with +y up; pixels are 0..n with +y DOWN. Getting this backwards produces a
  // mask that is a perfect mirror of the object, which reads as a segmentation failure
  // rather than a coordinate one.
  const toPx = (ndcX: number, ndcY: number): [number, number] => [
    ((ndcX + 1) / 2) * width,
    ((1 - ndcY) / 2) * height,
  ];
  const [px0, py1] = toPx(rect.x0, rect.y0);
  const [px1, py0] = toPx(rect.x1, rect.y1);

  const query = new URLSearchParams({
    x0: String(px0 * scale),
    y0: String(py0 * scale),
    x1: String(px1 * scale),
    y1: String(py1 * scale),
  });
  if (prompt.trim()) query.set("prompt", prompt.trim());

  let bitmap: ImageBitmap;
  try {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    const response = await fetch(`${ENDPOINT}/segment?${query}`, {
      method: "POST",
      body: view.blob,
      headers: { "Content-Type": "image/png" },
      signal: controller.signal,
    });
    clearTimeout(timer);
    if (!response.ok) return null;
    lastBoxSource = response.headers.get("X-Box-Source") ?? "";
    bitmap = await createImageBitmap(await response.blob());
  } catch {
    return null;
  }

  // Back to full device resolution, so the caller can index it with a projected splat
  // directly rather than carrying a scale factor around.
  const out = document.createElement("canvas");
  out.width = width;
  out.height = height;
  const context = out.getContext("2d", { willReadFrequently: true });
  if (!context) return null;
  context.drawImage(bitmap, 0, 0, width, height);
  bitmap.close();

  const pixels = context.getImageData(0, 0, width, height).data;
  const data = new Uint8Array(width * height);
  let set = 0;
  for (let i = 0; i < data.length; i++) {
    const on = pixels[i * 4] > 127 ? 1 : 0;
    data[i] = on;
    set += on;
  }
  // An empty mask is a failure dressed as a success: the caller must fall back rather than
  // select nothing at all.
  if (set === 0) return null;

  return { data, width, height };
}

/** Whether the last mask came from the sentence or from the raw drag. For the debug line. */
export let lastBoxSource = "";

/** The rendered view as a PNG, optionally downscaled. */
async function capture(canvas: HTMLCanvasElement, scale: number): Promise<Blob | null> {
  const source =
    scale >= 1
      ? canvas
      : (() => {
          const small = document.createElement("canvas");
          small.width = Math.max(1, Math.round(canvas.width * scale));
          small.height = Math.max(1, Math.round(canvas.height * scale));
          small.getContext("2d")?.drawImage(canvas, 0, 0, small.width, small.height);
          return small;
        })();

  return new Promise((resolve) => {
    source.toBlob((blob) => resolve(blob), "image/png");
  });
}

/** Is this NDC point inside the mask? */
export function masked(mask: Mask, ndcX: number, ndcY: number): boolean {
  const px = Math.round(((ndcX + 1) / 2) * mask.width);
  const py = Math.round(((1 - ndcY) / 2) * mask.height);
  if (px < 0 || py < 0 || px >= mask.width || py >= mask.height) return false;
  return mask.data[py * mask.width + px] === 1;
}
