/**
 * Tailwind is used for layout only.
 *
 * The visual language lives in `src/styles/tokens.css` as custom properties, because it is
 * a design system rather than a utility palette, and because a token with a comment
 * explaining WHY it exists is worth more than a shorthand. What is mapped here is what
 * genuinely benefits from a utility: colours that need opacity modifiers, and the spacing
 * scale.
 */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: { 900: "var(--ink-900)", 800: "var(--ink-800)", 700: "var(--ink-700)" },
        line: { DEFAULT: "var(--line)", bright: "var(--line-bright)" },
        text: { hi: "var(--text-hi)", mid: "var(--text-mid)", lo: "var(--text-lo)" },
        live: "var(--live)",
        warn: "var(--warn)",
      },
      fontFamily: {
        ui: "var(--font-ui)",
        num: "var(--font-num)",
      },
      transitionTimingFunction: {
        settle: "var(--ease-settle)",
        snap: "var(--ease-snap)",
      },
    },
  },
  plugins: [],
};
