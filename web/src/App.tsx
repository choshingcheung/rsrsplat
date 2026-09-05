/**
 * The whole interface.
 *
 * Full-bleed viewport, everything else floating over it. There is no layout to speak of and
 * that is the point: no header eating vertical space, no sidebar squeezing the scan. The
 * chrome sits in the corners, where an instrument puts its readings.
 */

import { useEffect } from "react";

import { activeController } from "./scene/controller";
import { Viewport } from "./scene/Viewport";
import { useScene } from "./store/scene";
import { Empty } from "./ui/Empty";
import { Frame } from "./ui/Frame";
import { ObjectList } from "./ui/ObjectList";
import { PromptBar } from "./ui/PromptBar";
import { Readout } from "./ui/Readout";

export default function App() {
  const phase = useScene((s) => s.phase);
  const dragging = useScene((s) => s.dragging);
  const running = useScene((s) => s.running);
  const promptKind = useScene((s) => s.prompt.kind);

  // Space and R, because during a demo one hand is gesturing at the screen. Suppressed while
  // the prompt has focus, or pausing the scene would be one word into a sentence.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (promptKind === "asking" || promptKind === "pending") return;
      if (event.target instanceof HTMLInputElement) return;
      const controller = activeController();
      if (event.code === "Space") {
        event.preventDefault();
        controller?.control(running ? "pause" : "play");
      }
      if (event.key.toLowerCase() === "r") controller?.control("reset");
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [running, promptKind]);

  return (
    <main
      className="relative h-full w-full overflow-hidden"
      style={{ background: "var(--ink-900)" }}
    >
      <Viewport />
      <Frame live={phase === "reading" || dragging} />
      <Readout />
      <Empty />
      <ObjectList />
      <PromptBar />
    </main>
  );
}
