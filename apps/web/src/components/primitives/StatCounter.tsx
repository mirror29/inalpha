"use client";

import { useEffect, useRef, useState } from "react";
import { animate, useInView, useReducedMotion } from "motion/react";

import { cn } from "@/lib/cn";
import { countUp } from "@/lib/motion";

interface StatCounterProps {
  /** Final value the counter animates to. */
  target: number;
  /** Optional text before the number (e.g., "★ "). */
  prefix?: string;
  /** Optional text after the number (e.g., " stars"). */
  suffix?: string;
  /** Seconds. Defaults to the shared `countUp.duration`. */
  duration?: number;
  className?: string;
}

/**
 * Count-up display. Triggers once when scrolled into view. Falls back to the
 * final value immediately under `prefers-reduced-motion`.
 */
export function StatCounter({
  target,
  prefix,
  suffix,
  duration = countUp.duration,
  className,
}: StatCounterProps) {
  const ref = useRef<HTMLSpanElement | null>(null);
  const inView = useInView(ref, { once: true, margin: "-80px" });
  const reduced = useReducedMotion();
  const [value, setValue] = useState(reduced ? target : 0);

  useEffect(() => {
    if (!inView || reduced) return;
    const controls = animate(0, target, {
      duration,
      ease: countUp.ease,
      onUpdate: (v) => setValue(Math.round(v)),
    });
    return () => controls.stop();
  }, [inView, reduced, target, duration]);

  return (
    <span ref={ref} className={cn("font-mono tabular-nums", className)}>
      {prefix}
      {value.toLocaleString()}
      {suffix}
    </span>
  );
}
