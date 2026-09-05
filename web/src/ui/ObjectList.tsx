/**
 * What is alive in the scene, and the transport.
 *
 * Bottom left, appearing only once something is physical — before that there is nothing to
 * list, and a panel sitting empty is a panel taking up room for no reason.
 *
 * Each object shows its parts and their joint types, in monospace, because a joint range is
 * a measurement. The accent marks an object as alive, which is its second and only other
 * meaning after "selected".
 */

import { activeController } from "../scene/controller";
import { useScene } from "../store/scene";
import type { PhysicsPart } from "../types/protocol";

/** Ranges cross the wire in degrees for a hinge and metres for a slide. Say which. */
function describe(part: PhysicsPart): string {
  if (!part.range) return part.jointType;
  const [lo, hi] = part.range;
  const unit = part.jointType === "hinge" ? "°" : "m";
  const value = part.jointType === "hinge" ? `${hi - lo}` : `${(hi - lo).toFixed(3)}`;
  return `${part.jointType} ${value}${unit}`;
}

export function ObjectList() {
  const controller = activeController();
  const objects = useScene((s) => s.objects);
  const running = useScene((s) => s.running);

  if (!objects.length) return null;

  return (
    <div className="rise absolute bottom-20 left-8 flex flex-col gap-3">
      {objects.map((object) => (
        <div key={object.id} className="energise pane min-w-[26ch] px-3 py-2">
          <div className="flex items-baseline justify-between gap-4">
            <span className="live" style={{ fontSize: "var(--size-read)" }}>
              {object.label}
            </span>
            <button
              type="button"
              onClick={() => controller?.remove(object.id)}
              className="tick"
              style={{ background: "none", border: "none", cursor: "pointer", padding: 0 }}
              aria-label={`remove ${object.label}`}
            >
              remove
            </button>
          </div>

          <div className="mt-1.5 flex flex-col gap-0.5">
            {object.parts.map((part) => (
              <div key={part.bodyName} className="flex items-baseline justify-between gap-4">
                <span
                  className="num"
                  style={{ fontSize: "var(--size-micro)", color: "var(--text-lo)" }}
                >
                  {part.bodyName.split("__").pop()}
                </span>
                <span
                  className="num"
                  style={{ fontSize: "var(--size-micro)", color: "var(--text-mid)" }}
                >
                  {describe(part)}
                </span>
              </div>
            ))}
          </div>

          <div
            className="mt-2 flex items-baseline justify-between gap-4"
            style={{ borderTop: "1px solid var(--line)", paddingTop: 6 }}
          >
            <span className="tick">mass</span>
            <span
              className="num"
              style={{ fontSize: "var(--size-tick)", color: "var(--text-mid)" }}
            >
              {object.massKg.toFixed(1)} kg
            </span>
          </div>
        </div>
      ))}

      <Transport running={running} />
    </div>
  );
}

/**
 * Play, pause, reset.
 *
 * Keyboard first — space and R — because during a demo one hand is gesturing at the screen.
 * The buttons exist so the shortcuts are discoverable, not the other way round.
 */
function Transport({ running }: { running: boolean }) {
  const controller = activeController();
  return (
    <div className="flex items-center gap-4">
      <button
        type="button"
        onClick={() => controller?.control(running ? "pause" : "play")}
        className="tick"
        style={{
          background: "none",
          border: "none",
          cursor: "pointer",
          padding: 0,
          color: running ? "var(--live)" : "var(--text-mid)",
        }}
      >
        {running ? "running · space" : "paused · space"}
      </button>
      <button
        type="button"
        onClick={() => controller?.control("reset")}
        className="tick"
        style={{ background: "none", border: "none", cursor: "pointer", padding: 0 }}
      >
        reset · r
      </button>
    </div>
  );
}
