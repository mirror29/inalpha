"use client";

import * as React from "react";
import { motion } from "motion/react";

import { cn } from "@/lib/cn";

interface BroadsheetSectionProps {
  /** Display ordinal — "01", "02". Rendered as mono in the header bar. */
  index: string;
  /** Section name — uppercase mono caption in the header bar. */
  eyebrow: string;
  /** Italic display serif title. Keep ≤ 12 words. */
  title: string;
  /** Optional sentence under the title. */
  intro?: string;
  /** Optional spec reference shown right-aligned in the header bar. */
  specRef?: string;
  /** Section content. */
  children: React.ReactNode;
  /** Override the title with a custom React node (e.g. inline accents). */
  titleNode?: React.ReactNode;
  className?: string;
}

/**
 * Editorial section header. See DESIGN.md §10.
 *
 * Single column, no oversized hangs. Three lines of structure:
 *   ─── hairline ────────────────────────────
 *   01 / SECTION NAME            FILE.md §X
 *   ─── hairline ────────────────────────────
 *
 * …then italic display title, body intro, and content. The visual
 * personality lives in the title, not the chrome.
 */
export function BroadsheetSection({
  index,
  eyebrow,
  title,
  intro,
  specRef,
  children,
  titleNode,
  className,
}: BroadsheetSectionProps) {
  return (
    <motion.section
      initial={{ opacity: 0, y: 12 }}
      whileInView={{ opacity: 1, y: 0 }}
      viewport={{ once: true, margin: "-120px" }}
      transition={{ duration: 0.5 }}
      className={cn("relative", className)}
    >
      {/* Bracketed header rule */}
      <div className="border-y border-fg/15">
        <div className="flex items-center justify-between gap-6 py-3 font-mono text-[11px] uppercase tracking-[0.22em] text-fg-muted">
          <span>
            <span className="text-fg/90">{index}</span>
            <span className="text-fg-muted/50"> / </span>
            <span>{eyebrow}</span>
          </span>
          {specRef ? (
            <span className="text-fg-muted/50">{specRef}</span>
          ) : null}
        </div>
      </div>

      <div className="grid grid-cols-12 gap-x-6 gap-y-6 pt-10 md:pt-14">
        <h2
          className="display-italic col-span-12 text-fg leading-[1.02] md:col-span-9"
          style={{
            fontSize: "clamp(2rem, 4.2vw, 3.25rem)",
            fontWeight: 400,
          }}
        >
          {titleNode ?? title}
        </h2>
        {intro ? (
          <p className="col-span-12 max-w-[62ch] text-[15px] leading-relaxed text-fg-muted md:col-span-9">
            {intro}
          </p>
        ) : null}
      </div>

      <div className="mt-12">{children}</div>
    </motion.section>
  );
}
