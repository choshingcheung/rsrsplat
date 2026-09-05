/**
 * The readings.
 *
 * An instrument's numbers, top right, right-aligned, tabular. Every value here is something
 * you would want to check before trusting what you are looking at: how many Gaussians came
 * out of the file, whether the frame rate is survivable, whether physics is attached.
 *
 * Right-aligned and tabular so the column edge never moves. A readout whose digits shift
 * position as the value changes is a fidget, not a measurement.
 */

import { useScene } from "../store/scene";

// `state` is explicitly nullable rather than optional: tsconfig has
// exactOptionalPropertyTypes, so a conditional expression producing undefined is not the
// same thing as omitting the prop.
function Row({
  label,
  value,
  state,
}: {
  label: string;
  value: string;
  state?: "live" | "warn" | undefined;
}) {
  const colour =
    state === "live" ? "var(--live)" : state === "warn" ? "var(--warn)" : "var(--text-hi)";
  return (
    <div className="flex items-baseline justify-end gap-3">
      <span className="tick">{label}</span>
      <span
        className="num tabular-nums"
        style={{ fontSize: "var(--size-read)", color: colour, minWidth: "8ch", textAlign: "right" }}
      >
        {value}
      </span>
    </div>
  );
}

export function Readout() {
  const { phase, splatCount, fps, frameMs, connected, progress } = useScene();

  if (phase === "empty") return null;

  return (
    <div className="rise stagger-1 absolute right-8 top-8 flex flex-col gap-1.5">
      {phase === "reading" ? (
        <Row label="reading" value={`${Math.round(progress * 100)}%`} state="live" />
      ) : (
        <Row label="gaussians" value={splatCount.toLocaleString()} />
      )}
      <Row
        label="fps"
        value={fps ? fps.toFixed(0) : "--"}
        state={fps && fps < 24 ? "warn" : undefined}
      />
      <Row label="frame" value={frameMs ? `${frameMs.toFixed(1)}ms` : "--"} />
      <Row
        label="physics"
        value={connected ? "linked" : "offline"}
        state={connected ? "live" : undefined}
      />
    </div>
  );
}
