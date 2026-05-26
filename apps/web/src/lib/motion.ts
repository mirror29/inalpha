import type { Variants } from "motion/react";

/**
 * Easing curves used across all Inalpha motion presets.
 * See DESIGN.md §6.1.
 */
export const EASE_OUT_QUART = [0.16, 1, 0.3, 1] as const;
export const EASE_IN_OUT_CIRC = [0.85, 0, 0.15, 1] as const;
export const EASE_STANDARD = [0.2, 0, 0, 1] as const;

/** Generic "rise in" — used for text and chips. */
export const fadeUp: Variants = {
  hidden: { opacity: 0, y: 24 },
  visible: {
    opacity: 1,
    y: 0,
    transition: { duration: 0.55, ease: EASE_OUT_QUART },
  },
};

/** Container stagger — drives children that share `variants={fadeUp}` etc. */
export const stagger: Variants = {
  hidden: {},
  visible: {
    transition: { staggerChildren: 0.06, delayChildren: 0.05 },
  },
};

/** Character-level container — wordmark only (see DESIGN.md §4.2). */
export const charStagger: Variants = {
  hidden: {},
  visible: {
    transition: { staggerChildren: 0.03 },
  },
};

export const charItem: Variants = {
  hidden: { opacity: 0, y: 12 },
  visible: {
    opacity: 1,
    y: 0,
    transition: { duration: 0.4, ease: EASE_OUT_QUART },
  },
};

/**
 * Side-slide with a hairline rotate. DualThesis pair uses this.
 * Pass `custom={-1}` for the left card and `custom={1}` for the right.
 */
export const slideInTilt: Variants = {
  hidden: (dir: number = -1) => ({
    opacity: 0,
    x: dir * -40,
    rotate: dir * -1,
  }),
  visible: {
    opacity: 1,
    x: 0,
    rotate: 0,
    transition: { duration: 0.6, ease: EASE_STANDARD },
  },
};

/**
 * Pulse dot — used by LiveBadge and AgentBubble status indicator.
 * Pure-CSS animation lives in globals.css `@keyframes pulse-glow`;
 * this variant only orchestrates fade-in.
 */
export const pulseDot: Variants = {
  hidden: { opacity: 0, scale: 0.6 },
  visible: {
    opacity: 1,
    scale: 1,
    transition: { duration: 0.4, ease: EASE_OUT_QUART },
  },
};

/**
 * SVG path stroke draw-on. Apply to `motion.path` with
 * `initial={{ pathLength: 0 }} animate={{ pathLength: 1 }}`.
 * This object captures the shared timing.
 */
export const pathDraw = {
  duration: 1.2,
  ease: EASE_STANDARD,
} as const;

/**
 * Count-up timing — consumed by StatCounter via the motion `useMotionValue`
 * + `animate(value, target, ...)` pattern. Not a Variants because we drive
 * a number directly, not opacity/transform.
 */
export const countUp = {
  duration: 2,
  ease: EASE_STANDARD,
} as const;

/**
 * Typewriter cadence — used by TerminalBlock when `typewriter` is on.
 * Time per character (ms). Linear by design — humans read code linearly.
 */
export const TYPEWRITER_MS_PER_CHAR = 18;

/**
 * Stagger container for grids (FeatureMatrix, KernelCards, chip rows).
 * Heavier delay than the generic `stagger` so card entrances feel deliberate.
 */
export const gridStagger: Variants = {
  hidden: {},
  visible: {
    transition: { staggerChildren: 0.08, delayChildren: 0.1 },
  },
};

/**
 * Card-style reveal — used inside FeatureMatrix and similar grid layouts.
 * Combines fade + rise + a touch of scale so an entire grid reads as
 * "snapping into place" instead of "all fading up at once."
 */
export const cardReveal: Variants = {
  hidden: { opacity: 0, y: 28, scale: 0.97 },
  visible: {
    opacity: 1,
    y: 0,
    scale: 1,
    transition: { duration: 0.55, ease: EASE_OUT_QUART },
  },
};

/**
 * Hover lift — apply via `whileHover` on motion components.
 * Visual contract: lift 2px, brighten by ~3%, smooth 200ms.
 */
export const liftHover = {
  y: -3,
  scale: 1.015,
  transition: { duration: 0.2, ease: EASE_OUT_QUART },
} as const;

/**
 * Tap feedback — light press-down for clickable cards / chips.
 */
export const tapPress = {
  scale: 0.98,
  transition: { duration: 0.1, ease: "easeOut" as const },
} as const;

/**
 * Section reveal — fades the whole block in from below with a wider amplitude
 * than fadeUp. Use sparingly on hero / kit-page section wrappers.
 */
export const sectionReveal: Variants = {
  hidden: { opacity: 0, y: 40 },
  visible: {
    opacity: 1,
    y: 0,
    transition: { duration: 0.7, ease: EASE_OUT_QUART },
  },
};
