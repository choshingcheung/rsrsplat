/**
 * A slider per joint: the interaction for everything that is fitted.
 *
 * Most of a room does not move under gravity. A dishwasher is plumbed in; a wall oven is
 * screwed to a cabinet. Force is the wrong verb for those — you don't push a dishwasher, you
 * **open its door**. So the interaction for an articulated part is to work the mechanism
 * directly, and a slider in the joint's own units is the honest control for that.
 *
 * Units are shown, always. A hinge reads in degrees and a drawer in millimetres, and a
 * number without a unit on a physics readout is a number you cannot check.
 */

import { useState } from "react";

import { activeController } from "../scene/controller";
import type { PhysicsPart } from "../types/protocol";

/** How a part's value reads. A drawer at 0.17 m printed as "0.2" is worse than useless. */
function format(part: PhysicsPart, value: number): string {
  if (part.jointType === "hinge") return `${value.toFixed(0)}°`;
  return `${(value * 1000).toFixed(0)}mm`;
}

export function JointSlider({ part }: { part: PhysicsPart }) {
  const [lo, hi] = part.range ?? [0, 0];
  // The rest position is the end nearer zero: a door starts shut, a button starts out.
  const [value, setValue] = useState(Math.abs(lo) <= Math.abs(hi) ? lo : hi);

  if (!part.range || part.jointType === "fixed" || part.jointType === "free") return null;

  const name = part.bodyName.split("__").pop();
  const fraction = hi === lo ? 0 : (value - lo) / (hi - lo);

  return (
    <div className="flex flex-col gap-1">
      <div className="flex items-baseline justify-between gap-4">
        <span className="num" style={{ fontSize: "var(--size-micro)", color: "var(--text-lo)" }}>
          {name}
        </span>
        <span
          className="num"
          style={{
            fontSize: "var(--size-micro)",
            color: fraction > 0.02 ? "var(--live)" : "var(--text-mid)",
          }}
        >
          {format(part, value)}
        </span>
      </div>

      <input
        type="range"
        min={lo}
        max={hi}
        step={(hi - lo) / 200}
        value={value}
        onChange={(event) => {
          const next = Number(event.target.value);
          setValue(next);
          // Every frame of the drag, not on release: the point is to watch the door swing.
          activeController()?.setJoint(part.bodyName, next);
        }}
        aria-label={`${name} ${part.jointType}`}
        style={{
          width: "100%",
          height: 2,
          appearance: "none",
          WebkitAppearance: "none",
          background: `linear-gradient(to right,
            var(--live) 0%, var(--live) ${fraction * 100}%,
            var(--line-bright) ${fraction * 100}%, var(--line-bright) 100%)`,
          outline: "none",
          cursor: "ew-resize",
        }}
      />
    </div>
  );
}
