/**
 * The viewfinder: corner brackets and an edge scale, drawn over the scan.
 *
 * This is the detail that does the most work in the whole interface. Four thin brackets and
 * a row of tick marks are enough to say "you are looking THROUGH something at a room",
 * which is the entire premise — and they cost no vertical space, cover almost no pixels,
 * and never push the viewport around the way a header bar would.
 *
 * Pointer events are off throughout: the frame is etched on the glass, not sitting on it.
 */

const CORNER = 26; // bracket arm length, px
const TICKS = 24; // marks along each edge

export function Frame({ live = false }: { live?: boolean }) {
  const stroke = live ? "var(--live-edge)" : "var(--line-bright)";

  return (
    <svg
      className="pointer-events-none absolute inset-0 h-full w-full"
      aria-hidden="true"
      style={{ transition: "opacity var(--t-base) var(--ease-settle)" }}
    >
      {/* Corner brackets. Drawn in user units off each corner so they hold their shape at
          any viewport size — a scaled bracket reads as a decorative frame, not an optic. */}
      {(
        [
          ["0", "0", 1, 1],
          ["100%", "0", -1, 1],
          ["0", "100%", 1, -1],
          ["100%", "100%", -1, -1],
        ] as const
      ).map(([x, y, dx, dy], i) => (
        <g key={i} transform={`translate(${x === "0" ? 18 : -18} ${y === "0" ? 18 : -18})`}>
          <path
            d={`M 0 ${CORNER * dy} L 0 0 L ${CORNER * dx} 0`}
            transform={`translate(${x} ${y})`}
            fill="none"
            stroke={stroke}
            strokeWidth="1"
            style={{ transition: "stroke var(--t-base) var(--ease-settle)" }}
          />
        </g>
      ))}

      {/* Edge scale. Every fifth mark is longer, the way a real rule is graduated. */}
      {Array.from({ length: TICKS }, (_, i) => {
        const t = (i + 1) / (TICKS + 1);
        const major = i % 5 === 4;
        const len = major ? 7 : 3.5;
        const opacity = major ? 0.5 : 0.26;
        return (
          <g key={`t${i}`} stroke={stroke} strokeWidth="1" opacity={opacity}>
            <line x1={`${t * 100}%`} y1="0" x2={`${t * 100}%`} y2={len} />
            <line x1={`${t * 100}%`} y1="100%" x2={`${t * 100}%`} y2={`calc(100% - ${len}px)`} />
          </g>
        );
      })}
      {Array.from({ length: Math.round(TICKS * 0.6) }, (_, i) => {
        const t = (i + 1) / (Math.round(TICKS * 0.6) + 1);
        const major = i % 5 === 4;
        const len = major ? 7 : 3.5;
        return (
          <g key={`s${i}`} stroke={stroke} strokeWidth="1" opacity={major ? 0.5 : 0.26}>
            <line x1="0" y1={`${t * 100}%`} x2={len} y2={`${t * 100}%`} />
            <line x1="100%" y1={`${t * 100}%`} x2={`calc(100% - ${len}px)`} y2={`${t * 100}%`} />
          </g>
        );
      })}
    </svg>
  );
}
