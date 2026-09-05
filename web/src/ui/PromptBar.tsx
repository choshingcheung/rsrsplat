/**
 * The prompt bar: the four seconds the whole product is about.
 *
 * It appears on selection commit, at the bottom centre — close to where the user's attention
 * already is, and not docked into a panel that would have been sitting there taking up space
 * beforehand. It disappears the moment the object exists.
 *
 * The placeholder teaches by example. "Describe how it should behave" tells a first-time
 * user nothing; *"a dishwasher, the door hinges at the bottom"* tells them the entire
 * vocabulary in one line.
 */

import { useEffect, useRef, useState } from "react";

import { activeController } from "../scene/controller";
import { useScene } from "../store/scene";

const PLACEHOLDERS = [
  "a wooden crate, heavy, sits flat",
  "a dishwasher, the door hinges at the bottom and opens ninety degrees",
  "a pedal bin, the lid hinges at the back",
  "a chest of drawers, built in",
];

export function PromptBar() {
  const controller = activeController();
  const prompt = useScene((s) => s.prompt);
  const [text, setText] = useState("");
  const input = useRef<HTMLInputElement>(null);
  const [placeholder] = useState(
    () => PLACEHOLDERS[Math.floor(Math.random() * PLACEHOLDERS.length)],
  );

  // Focus the moment it appears, so the gesture flows straight into typing.
  useEffect(() => {
    if (prompt.kind === "asking") {
      setText("");
      input.current?.focus();
    }
  }, [prompt.kind]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") controller?.clearSelection();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [controller]);

  if (prompt.kind === "idle") return null;

  const pending = prompt.kind === "pending";
  const failed = prompt.kind === "failed";

  return (
    <div className="rise pointer-events-none absolute inset-x-0 bottom-10 flex justify-center px-8">
      <div
        className={`pane pointer-events-auto flex w-full max-w-[62ch] flex-col gap-2 px-4 py-3 ${
          failed ? "" : "live-border"
        }`}
        style={failed ? { borderColor: "var(--warn)" } : undefined}
      >
        <div className="flex items-baseline justify-between gap-4">
          <span className="tick">{failed ? "could not build that" : "describe it"}</span>
          {prompt.kind === "asking" ? (
            <span
              className="num"
              style={{ fontSize: "var(--size-tick)", color: "var(--live)" }}
            >
              {prompt.splatCount.toLocaleString()} selected
            </span>
          ) : null}
        </div>

        {failed ? (
          <p style={{ color: "var(--warn)", fontSize: "var(--size-read)" }}>{prompt.reason}</p>
        ) : null}

        <form
          onSubmit={(event) => {
            event.preventDefault();
            const value = text.trim();
            if (value && !pending) controller?.physicalize(value);
          }}
        >
          <input
            ref={input}
            value={pending ? prompt.prompt : text}
            disabled={pending}
            onChange={(event) => setText(event.target.value)}
            placeholder={placeholder}
            spellCheck={false}
            autoComplete="off"
            className={pending ? "pending" : ""}
            style={{
              width: "100%",
              background: "transparent",
              border: "none",
              outline: "none",
              color: "var(--text-hi)",
              fontFamily: "var(--font-ui)",
              fontSize: "var(--size-lead)",
            }}
          />
        </form>

        <div className="flex items-center justify-between">
          <span className="tick">
            {pending ? "building" : failed ? "try again, or esc to cancel" : "enter to build"}
          </span>
          <button
            type="button"
            onClick={() => controller?.clearSelection()}
            className="tick"
            style={{ background: "none", border: "none", cursor: "pointer", padding: 0 }}
          >
            esc
          </button>
        </div>
      </div>
    </div>
  );
}
