import type { Config } from "tailwindcss";

/**
 * The palette is derived, not chosen: each connector carries its own ink, and
 * that ink follows its evidence everywhere it appears — the citation marker in
 * the prose, the entry in the rail, the row in a routing table. Colour here
 * means provenance and nothing else.
 *
 * Two consequences worth stating, because both are easy to undo by accident:
 *
 *   1. The agent trace is NOT coloured. A pipeline node is not a source, so
 *      giving `synthesize` an ink would be decoration wearing the costume of
 *      information.
 *   2. Grade colours (ok/warn/bad) are a separate three-value semantic scale,
 *      used only on `card`, where each clears 4.5:1. On `board` they do not,
 *      so they must not be placed there.
 *
 * Every value below was measured against its intended background rather than
 * eyeballed. A mid-tone ground is unforgiving that way: the greys that look
 * right on white fail on it.
 */
const config: Config = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}", "./lib/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        board: "#C2C6BC", // archival board — the ground
        boardDeep: "#AFB4A8", // recessed panels, e.g. the trace strip
        card: "#FBFBF9", // the mounted exhibit
        ink: "#1A1D19", // 9.81:1 on board
        ink2: "#3F443E", // 5.74:1 on board — secondary prose
        ink3: "#4E534B", // 4.54:1 on board — labels, file paths, small text
        rule: "#9BA097", // separators only, never text
        ruleSoft: "#D6D9D1",

        // source inks
        gh: "#24292F",
        jira: "#0B4FC4",
        slack: "#66185F",

        // retrieval grade — card surfaces only
        ok: "#2C6B45", // 6.15:1 on card
        warn: "#7A5E12", // 5.90:1 on card
        bad: "#A8342A" // 6.35:1 on card
      },
      fontFamily: {
        sans: ["var(--font-sans)", "ui-sans-serif", "system-ui", "sans-serif"],
        serif: ["var(--font-serif)", "Georgia", "serif"],
        mono: ["var(--font-mono)", "ui-monospace", "Menlo", "monospace"]
      },
      boxShadow: {
        // A paste-up shadow, hard-edged rather than blurred: the card is
        // mounted on board, not floating above a screen.
        mount: "10px 10px 0 rgba(26, 29, 25, 0.14)",
        mountSm: "6px 6px 0 rgba(26, 29, 25, 0.16)",
        mountBad: "10px 10px 0 rgba(168, 52, 42, 0.16)"
      }
    }
  },
  plugins: []
};

export default config;
