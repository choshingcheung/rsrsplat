/**
 * The scene's identity, once there is one.
 *
 * A label in a corner: which scan is open, and the gesture that works on it. The entry
 * screen that used to live here is now `Landing`, because asking someone to find a 127 MB
 * file on disk before they have seen the thing work is the wrong first move.
 */

import { useScene } from "../store/scene";

export function Empty() {
  const { phase, fileName, error } = useScene();

  // The entry screen belongs to `Landing`. This is only the identity line that replaces it
  // once a scan is open.
  if (phase === "empty") return null;

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
