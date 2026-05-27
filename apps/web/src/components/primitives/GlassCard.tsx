import * as React from "react";

import { cn } from "@/lib/cn";

/**
 * Tint controls the hover-state border color. Body remains the same.
 * See DESIGN.md §7.2 — used by Hero LiveDebatePanel and DualThesis pair.
 */
const tintMap = {
  cyan: "hover:border-cyan/40",
  fox: "hover:border-fox-red/40",
  bull: "hover:border-bull/40",
  gold: "hover:border-gold/40",
  neutral: "hover:border-fg-muted/40",
} as const;

export type GlassCardTint = keyof typeof tintMap;

interface GlassCardProps extends React.HTMLAttributes<HTMLDivElement> {
  /**
   * Hover accent — defaults to `neutral`. Use `cyan` for Agent-First column,
   * `gold` for Engineering-discipline column, `fox` for opposing-stance UI.
   */
  tint?: GlassCardTint;
}

/**
 * Backdrop-blurred card used by Hero widget and DualThesis pair.
 * No internal padding choice — caller controls inner spacing via className.
 */
export function GlassCard({
  tint = "neutral",
  className,
  children,
  ...rest
}: GlassCardProps) {
  return (
    <div
      className={cn(
        "rounded-xl border border-border-subtle bg-bg-elev/40 backdrop-blur-md transition-colors",
        tintMap[tint],
        className,
      )}
      {...rest}
    >
      {children}
    </div>
  );
}
