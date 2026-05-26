import { cn } from "@/lib/cn";

/**
 * Visual rule — see DESIGN.md §7.2.
 * `gold` is the default for CurrentState's "alpha quality" label.
 */
const tintMap = {
  gold: { dot: "bg-gold", border: "border-gold/40", fill: "bg-gold/10", text: "text-gold" },
  cyan: { dot: "bg-cyan", border: "border-cyan/40", fill: "bg-cyan/10", text: "text-cyan" },
  fox: {
    dot: "bg-fox-red",
    border: "border-fox-red/40",
    fill: "bg-fox-red/10",
    text: "text-fox-red",
  },
  bull: { dot: "bg-bull", border: "border-bull/40", fill: "bg-bull/10", text: "text-bull" },
} as const;

export type LiveBadgeTint = keyof typeof tintMap;

interface LiveBadgeProps {
  label: string;
  tint?: LiveBadgeTint;
  className?: string;
}

/**
 * Inline pill with a pulsing status dot.
 * Animation comes from the `.pulse-glow` utility in globals.css —
 * no motion variant needed (CSS-only, prefers-reduced-motion safe).
 */
export function LiveBadge({ label, tint = "gold", className }: LiveBadgeProps) {
  const t = tintMap[tint];
  return (
    <span
      className={cn(
        "inline-flex items-center gap-2 rounded-full border px-3 py-1 font-mono text-xs",
        t.border,
        t.fill,
        t.text,
        className,
      )}
    >
      <span aria-hidden className={cn("size-1.5 rounded-full pulse-glow", t.dot)} />
      {label}
    </span>
  );
}
