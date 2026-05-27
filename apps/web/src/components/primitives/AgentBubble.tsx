"use client";

import * as React from "react";

import { cn } from "@/lib/cn";

/**
 * Role -> visual mapping per DESIGN.md §3.2:
 *   bull     → green (international convention)
 *   bear     → fox-red (also brand color — opposing stance)
 *   research → cyan-dim
 *   risk     → gold
 */
const roleMap = {
  bull: { border: "border-bull/40", label: "text-bull", glow: "shadow-bull/10" },
  bear: { border: "border-fox-red/40", label: "text-fox-red", glow: "shadow-fox-red/10" },
  research: { border: "border-cyan/40", label: "text-cyan", glow: "shadow-cyan/10" },
  risk: { border: "border-gold/40", label: "text-gold", glow: "shadow-gold/10" },
} as const;

const statusDotMap = {
  idle: "bg-fg-muted/40",
  thinking: "bg-cyan pulse-glow",
  done: "bg-bull",
} as const;

export type AgentRole = keyof typeof roleMap;
export type AgentStatus = keyof typeof statusDotMap;

interface AgentBubbleProps {
  role: AgentRole;
  status?: AgentStatus;
  className?: string;
  /** Optional override of the visible role name. Defaults to `role`. */
  label?: string;
  children?: React.ReactNode;
}

/**
 * Named container for a single agent's output in AgentDebateDemo.
 * Border color signals role; small dot signals lifecycle.
 */
export function AgentBubble({
  role,
  status = "idle",
  label,
  className,
  children,
}: AgentBubbleProps) {
  const r = roleMap[role];
  return (
    <article
      className={cn(
        "relative rounded-lg border bg-bg-elev/40 p-4 backdrop-blur-md transition-colors",
        r.border,
        className,
      )}
    >
      <header className="mb-3 flex items-center justify-between gap-3">
        <span className={cn("font-mono text-xs uppercase tracking-[0.18em]", r.label)}>
          {label ?? role}
        </span>
        <span className="inline-flex items-center gap-1.5 font-mono text-[10px] text-fg-muted">
          <span aria-hidden className={cn("size-1.5 rounded-full", statusDotMap[status])} />
          {status}
        </span>
      </header>
      <div className="text-sm leading-relaxed text-fg-muted">{children}</div>
    </article>
  );
}
