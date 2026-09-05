/**
 * The whole interface.
 *
 * Full-bleed viewport, everything else floating over it. There is no layout to speak of and
 * that is the point: no header eating vertical space, no sidebar squeezing the scan. The
 * chrome sits in the corners, where an instrument puts its readings.
 */

import { Viewport } from "./scene/Viewport";
import { useScene } from "./store/scene";
import { Empty } from "./ui/Empty";
import { Frame } from "./ui/Frame";
import { Readout } from "./ui/Readout";

export default function App() {
  const phase = useScene((s) => s.phase);

  return (
    <main className="relative h-full w-full overflow-hidden" style={{ background: "var(--ink-900)" }}>
      <Viewport />
      <Frame live={phase === "reading"} />
      <Readout />
      <Empty />
    </main>
  );
}
