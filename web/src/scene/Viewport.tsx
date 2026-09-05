/**
 * The viewport, and the two gestures it answers to.
 *
 * Orbit is a plain drag, because looking around is what you do most. **Shift-drag selects**,
 * which is the convention in every 3D tool and avoids a mode — a mode has to be indicated,
 * remembered, and toggled back, all while someone is talking to an audience.
 *
 * Everything three-dimensional lives in `SceneController`. This component exists to mount
 * it, forward pointer events, and draw the selection rectangle, which is a DOM element
 * because a two-pixel outline does not need a render pass.
 */

import { useEffect, useRef, useState } from "react";

import { useScene } from "../store/scene";
import { SceneController } from "./controller";

/** The live rectangle, in client pixels. */
interface DragBox {
  left: number;
  top: number;
  width: number;
  height: number;
}

export function Viewport({ onReady }: { onReady?: (controller: SceneController) => void }) {
  const host = useRef<HTMLDivElement>(null);
  const controller = useRef<SceneController | null>(null);
  const anchor = useRef<{ x: number; y: number } | null>(null);
  const [box, setBox] = useState<DragBox | null>(null);

  useEffect(() => {
    const container = host.current;
    if (!container) return;

    const scene = new SceneController(container);
    controller.current = scene;
    onReady?.(scene);

    return () => {
      scene.dispose();
      controller.current = null;
    };
    // Mounting once is the point: a re-created renderer would drop the loaded capture.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // --- drag and drop -------------------------------------------------------------------
  useEffect(() => {
    const container = host.current;
    if (!container) return;

    const stop = (event: DragEvent) => {
      event.preventDefault();
      event.stopPropagation();
    };
    const onDrop = (event: DragEvent) => {
      stop(event);
      const file = event.dataTransfer?.files?.[0];
      if (file) void controller.current?.load(file);
    };

    container.addEventListener("dragover", stop);
    container.addEventListener("dragenter", stop);
    container.addEventListener("drop", onDrop);
    return () => {
      container.removeEventListener("dragover", stop);
      container.removeEventListener("dragenter", stop);
      container.removeEventListener("drop", onDrop);
    };
  }, []);

  // --- selection gesture ---------------------------------------------------------------
  useEffect(() => {
    const container = host.current;
    if (!container) return;

    const local = (event: PointerEvent) => {
      const rect = container.getBoundingClientRect();
      return { x: event.clientX - rect.left, y: event.clientY - rect.top };
    };

    const onDown = (event: PointerEvent) => {
      if (!event.shiftKey || event.button !== 0) return;
      const at = local(event);
      anchor.current = at;
      setBox({ left: at.x, top: at.y, width: 0, height: 0 });
      controller.current?.beginDrag(at.x, at.y);
      container.setPointerCapture(event.pointerId);
    };

    const onMove = (event: PointerEvent) => {
      if (!anchor.current) return;
      const at = local(event);
      const from = anchor.current;
      setBox({
        left: Math.min(from.x, at.x),
        top: Math.min(from.y, at.y),
        width: Math.abs(at.x - from.x),
        height: Math.abs(at.y - from.y),
      });
      controller.current?.updateDrag(at.x, at.y);
    };

    const onUp = (event: PointerEvent) => {
      if (!anchor.current) return;
      anchor.current = null;
      setBox(null);
      controller.current?.endDrag();
      if (container.hasPointerCapture(event.pointerId)) {
        container.releasePointerCapture(event.pointerId);
      }
    };

    container.addEventListener("pointerdown", onDown);
    container.addEventListener("pointermove", onMove);
    container.addEventListener("pointerup", onUp);
    container.addEventListener("pointercancel", onUp);
    return () => {
      container.removeEventListener("pointerdown", onDown);
      container.removeEventListener("pointermove", onMove);
      container.removeEventListener("pointerup", onUp);
      container.removeEventListener("pointercancel", onUp);
    };
  }, []);

  return (
    <div ref={host} className="absolute inset-0">
      {box ? <SelectionRect box={box} /> : null}
    </div>
  );
}

/**
 * The rectangle, with its count riding on the corner.
 *
 * Corner ticks rather than a filled overlay: a tinted rectangle over a photoreal scan hides
 * the thing you are trying to aim at, which is the one thing it must not do.
 */
function SelectionRect({ box }: { box: DragBox }) {
  const count = useScene((s) => s.hoverCount);

  return (
    <div
      className="pointer-events-none absolute"
      style={{
        left: box.left,
        top: box.top,
        width: box.width,
        height: box.height,
        border: "1px solid var(--live)",
        background: "var(--live-glow)",
      }}
    >
      {(
        [
          ["-1px", "-1px", "border-left border-top"],
          ["auto", "-1px", "border-right border-top"],
          ["-1px", "auto", "border-left border-bottom"],
          ["auto", "auto", "border-right border-bottom"],
        ] as const
      ).map(([left, top, side], i) => (
        <span
          key={i}
          className={side}
          style={{
            position: "absolute",
            left: left === "auto" ? undefined : left,
            right: left === "auto" ? "-1px" : undefined,
            top: top === "auto" ? undefined : top,
            bottom: top === "auto" ? "-1px" : undefined,
            width: 9,
            height: 9,
            borderColor: "var(--live)",
            borderWidth: 2,
            borderStyle: "solid",
          }}
        />
      ))}

      {box.width > 40 ? (
        <span
          className="num"
          style={{
            position: "absolute",
            left: 0,
            top: "100%",
            marginTop: 6,
            fontSize: "var(--size-tick)",
            color: "var(--live)",
            whiteSpace: "nowrap",
          }}
        >
          {count.toLocaleString()}
        </span>
      ) : null}
    </div>
  );
}
