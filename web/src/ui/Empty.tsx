/**
 * The empty state, and the scene's identity once there is one.
 *
 * The empty state teaches by example rather than by instruction: it shows the two sentences
 * that make the product work, because "describe how it should behave" tells a first-time
 * user nothing and *"a dishwasher, the door hinges at the bottom"* tells them everything.
 *
 * Nothing here is centred in a hero. An instrument's idle state is a label in a corner and
 * a lot of empty glass.
 */

import { useScene } from "../store/scene";

const EXAMPLES = [
  "a wooden crate, heavy, sits flat",
  "a dishwasher, the door hinges at the bottom and opens ninety degrees",
];

export function Empty() {
  const { phase, fileName, error } = useScene();

  if (phase === "empty") {
    return (
      <div className="pointer-events-none absolute inset-0 flex items-end p-8">
        <div className="rise stagger-2 max-w-[42ch]">
          <div className="tick mb-3">rsrsplat</div>
          <p style={{ fontSize: "var(--size-lead)", color: "var(--text-hi)", lineHeight: 1.5 }}>
            Drop a <span className="num">.ply</span> scan anywhere.
          </p>
          <p className="mt-2" style={{ color: "var(--text-mid)" }}>
            Then <span style={{ color: "var(--text-hi)" }}>shift-drag</span> a box over
            something in it and say what it is.
          </p>

          <div className="mt-6 flex flex-col gap-1.5">
            {EXAMPLES.map((example) => (
              <div
                key={example}
                className="num"
                style={{
                  fontSize: "var(--size-tick)",
                  color: "var(--text-lo)",
                  paddingLeft: "12px",
                  borderLeft: "1px solid var(--line-bright)",
                }}
              >
                {example}
              </div>
            ))}
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="fade absolute bottom-8 left-8 flex items-baseline gap-3">
      <span className="tick">scene</span>
      <span className="num" style={{ fontSize: "var(--size-read)", color: "var(--text-mid)" }}>
        {fileName}
      </span>
      {/* The gesture, still visible once there is something to use it on. */}
      <span className="tick">shift-drag to select · drag an object to throw it</span>
      {error ? (
        <span className="num" style={{ fontSize: "var(--size-read)", color: "var(--warn)" }}>
          {error}
        </span>
      ) : null}
    </div>
  );
}
