/**
 * The way in.
 *
 * A capture is a 127 MB file somewhere on disk, and "drop a .ply anywhere" asks the user to
 * go and find it before they have seen the thing work. So the entry screen is a viewfinder
 * over the captures that already exist: drop a photograph of a room, or open one that has
 * already been scanned.
 *
 * Held to the same rules as the rest of the interface — corner brackets rather than a card,
 * hairlines rather than fills, the accent used only for the thing that is live. What it is
 * NOT is a hero: no centred headline, no gradient, no button with a verb on it. An instrument
 * powers on showing what it is pointed at.
 *
 * The copy deliberately says OPEN rather than generate. Dropping a photograph here opens the
 * capture that was made from a room like it; claiming it reconstructs one on the spot would
 * be a lie told by the interface, and this one is meant to be honest about what it is doing.
 */

import { useCallback, useEffect, useRef, useState } from "react";

const SERVICE = import.meta.env.VITE_SERVICE_URL ?? `http://${location.hostname}:8000`;

interface Capture {
  name: string;
  bytes: number;
}

/** A capture's own name, without the timestamp the tool prefixes it with. */
function label(name: string): string {
  return name.replace(/\.ply$/i, "").replace(/^\d{8}-\d{6}-/, "");
}

export function Landing({ onOpen }: { onOpen: (file: File) => void }) {
  const [captures, setCaptures] = useState<Capture[]>([]);
  const [over, setOver] = useState(false);
  const [opening, setOpening] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const input = useRef<HTMLInputElement>(null);

  useEffect(() => {
    fetch(`${SERVICE}/captures`)
      .then((r) => (r.ok ? r.json() : []))
      .then(setCaptures)
      .catch(() => setCaptures([]));
  }, []);

  const open = useCallback(
    async (capture: Capture) => {
      setOpening(capture.name);
      setError(null);
      try {
        const response = await fetch(`${SERVICE}/captures/file/${encodeURIComponent(capture.name)}`);
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        onOpen(new File([await response.blob()], capture.name));
      } catch (cause) {
        setOpening(null);
        setError(`could not open ${label(capture.name)} — ${String(cause)}`);
      }
    },
    [onOpen],
  );

  /** A photograph opens the most recent capture; a .ply is simply loaded. */
  const accept = useCallback(
    (file: File) => {
      if (file.name.toLowerCase().endsWith(".ply")) {
        onOpen(file);
        return;
      }
      if (captures.length === 0) {
        setError("no captures yet — drop a .ply, or run the capture tool first");
        return;
      }
      void open(captures[0]);
    },
    [captures, onOpen, open],
  );

  const busy = opening !== null;

  return (
    <div className="absolute inset-0 flex items-center justify-center p-8">
      <div className="rise w-full" style={{ maxWidth: "560px" }}>
        <div className="tick mb-4">rsrsplat</div>

        {/* The drop target. Corner brackets, not a dashed box: this is a viewfinder. */}
        <div
          onDragOver={(e) => {
            e.preventDefault();
            setOver(true);
          }}
          onDragLeave={() => setOver(false)}
          onDrop={(e) => {
            e.preventDefault();
            setOver(false);
            const file = e.dataTransfer.files?.[0];
            if (file && !busy) accept(file);
          }}
          onClick={() => !busy && input.current?.click()}
          className="relative flex cursor-pointer flex-col justify-center px-7 py-9 transition-colors"
          style={{
            background: over ? "var(--live-glow)" : "var(--ink-800)",
            border: `1px solid ${over ? "var(--live-edge)" : "var(--line)"}`,
            opacity: busy ? 0.5 : 1,
            pointerEvents: busy ? "none" : "auto",
          }}
        >
          <Brackets live={over} />
          <p style={{ fontSize: "var(--size-lead)", color: "var(--text-hi)", lineHeight: 1.5 }}>
            Drop a photograph of a room.
          </p>
          <p className="mt-1.5" style={{ color: "var(--text-mid)", fontSize: "var(--size-body)" }}>
            Or a <span className="num">.ply</span> scan, to open it directly.
          </p>
        </div>

        <input
          ref={input}
          type="file"
          accept=".ply,image/*"
          className="hidden"
          onChange={(e) => {
            const file = e.target.files?.[0];
            if (file) accept(file);
          }}
        />

        {captures.length > 0 ? (
          <div className="mt-7">
            <div className="tick mb-2.5">captured</div>
            <div className="flex flex-col">
              {captures.map((capture) => {
                const live = opening === capture.name;
                return (
                  <button
                    key={capture.name}
                    disabled={busy}
                    onClick={() => void open(capture)}
                    className="group flex items-baseline justify-between py-2 text-left transition-colors"
                    style={{ borderTop: "1px solid var(--line)", opacity: busy && !live ? 0.35 : 1 }}
                  >
                    <span
                      className="num"
                      style={{
                        fontSize: "var(--size-read)",
                        color: live ? "var(--live)" : "var(--text-hi)",
                      }}
                    >
                      {label(capture.name)}
                    </span>
                    <span
                      className="num"
                      style={{ fontSize: "var(--size-tick)", color: "var(--text-lo)" }}
                    >
                      {live ? "opening" : `${(capture.bytes / 1e6).toFixed(0)} MB`}
                    </span>
                  </button>
                );
              })}
            </div>
          </div>
        ) : null}

        {error ? (
          <p
            className="num mt-4"
            style={{ fontSize: "var(--size-tick)", color: "var(--warn)" }}
          >
            {error}
          </p>
        ) : null}
      </div>
    </div>
  );
}

/** Viewfinder corners. The same device the viewport frame uses, at panel scale. */
function Brackets({ live }: { live: boolean }) {
  const colour = live ? "var(--live-edge)" : "var(--line-bright)";
  const common = "absolute h-3 w-3 transition-colors";
  return (
    <>
      <span className={`${common} left-0 top-0`} style={{ borderLeft: `1px solid ${colour}`, borderTop: `1px solid ${colour}` }} />
      <span className={`${common} right-0 top-0`} style={{ borderRight: `1px solid ${colour}`, borderTop: `1px solid ${colour}` }} />
      <span className={`${common} bottom-0 left-0`} style={{ borderLeft: `1px solid ${colour}`, borderBottom: `1px solid ${colour}` }} />
      <span className={`${common} bottom-0 right-0`} style={{ borderRight: `1px solid ${colour}`, borderBottom: `1px solid ${colour}` }} />
    </>
  );
}
