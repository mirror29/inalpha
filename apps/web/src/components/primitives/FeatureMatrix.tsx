"use client";

import * as React from "react";
import { motion } from "motion/react";
import type { LucideIcon } from "lucide-react";

import { cn } from "@/lib/cn";
import { cardReveal, gridStagger, liftHover, tapPress } from "@/lib/motion";

type Accent = "cyan" | "fox" | "gold" | "bull";

export interface FeatureItem {
  icon: LucideIcon;
  title: string;
  description: string;
  /** Optional supporting metric line (e.g., "12 venues", "AGPL-3.0"). */
  caption?: string;
  accent?: Accent;
}

interface FeatureMatrixProps {
  items: FeatureItem[];
  columns?: 2 | 3 | 4;
  className?: string;
}

/** See DESIGN.md §10.6 — used by EngineeringHarness + tech-highlight grids. */
const accentMap: Record<
  Accent,
  {
    iconBg: string;
    iconFg: string;
    hover: string;
    chip: string;
    glow: string;
  }
> = {
  cyan: {
    iconBg: "bg-cyan/10",
    iconFg: "text-cyan",
    hover: "hover:border-cyan/50",
    chip: "text-cyan",
    glow: "0 0 32px -10px rgba(95,179,255,0.55)",
  },
  fox: {
    iconBg: "bg-fox-red/10",
    iconFg: "text-fox-red",
    hover: "hover:border-fox-red/50",
    chip: "text-fox-red",
    glow: "0 0 32px -10px rgba(200,70,60,0.55)",
  },
  gold: {
    iconBg: "bg-gold/10",
    iconFg: "text-gold",
    hover: "hover:border-gold/50",
    chip: "text-gold",
    glow: "0 0 32px -10px rgba(212,167,68,0.55)",
  },
  bull: {
    iconBg: "bg-bull/10",
    iconFg: "text-bull",
    hover: "hover:border-bull/50",
    chip: "text-bull",
    glow: "0 0 32px -10px rgba(74,222,128,0.55)",
  },
};

const colsMap = {
  2: "md:grid-cols-2",
  3: "md:grid-cols-3",
  4: "md:grid-cols-4",
} as const;

/**
 * Grid of icon + title + description cards.
 * - Stagger-reveals on scroll into view (gridStagger + cardReveal).
 * - Each card lifts and glows on hover (liftHover + accent box-shadow).
 * - Icon chip rotates a touch on hover for tactile feedback.
 */
export function FeatureMatrix({ items, columns = 3, className }: FeatureMatrixProps) {
  return (
    <motion.div
      className={cn(
        "grid gap-px overflow-hidden rounded-xl border border-border-subtle bg-border-subtle",
        colsMap[columns],
        className,
      )}
      variants={gridStagger}
      initial="hidden"
      whileInView="visible"
      viewport={{ once: true, margin: "-80px" }}
    >
      {items.map((it, i) => {
        const a = accentMap[it.accent ?? "cyan"];
        const Icon = it.icon;
        return (
          <motion.article
            key={`${it.title}-${i}`}
            variants={cardReveal}
            whileHover={liftHover}
            whileTap={tapPress}
            className={cn(
              "group relative flex flex-col gap-3 bg-bg-deep p-5 transition-colors duration-200",
              a.hover,
            )}
            style={{ willChange: "transform" }}
            onMouseEnter={(e) => {
              e.currentTarget.style.boxShadow = a.glow;
            }}
            onMouseLeave={(e) => {
              e.currentTarget.style.boxShadow = "";
            }}
          >
            <header className="flex items-center justify-between">
              <motion.span
                className={cn(
                  "inline-flex size-9 items-center justify-center rounded-md",
                  a.iconBg,
                  a.iconFg,
                )}
                aria-hidden
                whileHover={{ rotate: -8, scale: 1.08 }}
                transition={{ type: "spring", stiffness: 320, damping: 18 }}
              >
                <Icon size={17} strokeWidth={1.5} />
              </motion.span>
              {it.caption ? (
                <span
                  className={cn(
                    "font-mono text-[10px] uppercase tracking-[0.15em]",
                    a.chip,
                  )}
                >
                  {it.caption}
                </span>
              ) : null}
            </header>
            <h3 className="font-mono text-sm text-fg transition-colors group-hover:text-fg">
              {it.title}
            </h3>
            <p className="text-xs leading-relaxed text-fg-muted">{it.description}</p>
            {/* Bottom accent line — slides in on hover */}
            <span
              aria-hidden
              className={cn(
                "absolute inset-x-5 bottom-0 h-px origin-left scale-x-0 transition-transform duration-300 group-hover:scale-x-100",
                {
                  cyan: "bg-cyan/60",
                  fox: "bg-fox-red/60",
                  gold: "bg-gold/60",
                  bull: "bg-bull/60",
                }[it.accent ?? "cyan"],
              )}
            />
          </motion.article>
        );
      })}
    </motion.div>
  );
}
