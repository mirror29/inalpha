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
  /** 对齐方向 —— "right" 时标题/正文/dateline 靠右，整页形成左右交错。 */
  align?: "left" | "right";
  /** 破格巨型序号在哪侧。默认跟随 align（right 对齐→序号左上）；可单独覆盖。 */
  indexSide?: "left" | "right";
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
  align = "left",
  indexSide,
  className,
}: BroadsheetSectionProps) {
  const right = align === "right";
  const numeralLeft = (indexSide ?? (right ? "left" : "right")) === "left";
  return (
    <motion.section
      initial={{ opacity: 0, y: 12 }}
      whileInView={{ opacity: 1, y: 0 }}
      viewport={{ once: true, margin: "-120px" }}
      transition={{ duration: 0.5 }}
      className={cn("group relative isolate overflow-hidden", className)}
    >
      {/* 破格巨型序号底纹 —— hover 整屏时金色高亮；右对齐时移到左上 */}
      <span
        aria-hidden
        className={cn(
          "pointer-events-none absolute -top-16 -z-10 select-none font-display italic leading-none text-fg/[0.04] transition-colors duration-500 group-hover:text-gold/25",
          numeralLeft ? "-left-2" : "-right-2",
        )}
        style={{ fontSize: "clamp(8rem, 24vw, 22rem)" }}
      >
        {index}
      </span>

      {/* Bracketed header rule */}
      <div className="border-y border-fg/15">
        <div
          className={cn(
            "flex items-center justify-between gap-6 py-3 font-mono text-[11px] uppercase tracking-[0.22em] text-fg-muted",
            right && "flex-row-reverse",
          )}
        >
          <span className="flex items-center gap-2.5">
            <span
              className="inline-block h-3 w-[2px] bg-seal/70"
              aria-hidden="true"
            />
            <span>{eyebrow}</span>
          </span>
          {specRef ? (
            <span className="text-fg-muted/50">{specRef}</span>
          ) : null}
        </div>
      </div>

      <div className="grid grid-cols-12 gap-x-6 gap-y-6 pt-10 md:pt-14">
        <h2
          className={cn(
            "display-italic col-span-12 text-fg leading-[1.02] md:col-span-9",
            right && "md:col-start-4 md:text-right",
          )}
          style={{
            fontSize: "clamp(2rem, 4.2vw, 3.25rem)",
            fontWeight: 400,
          }}
        >
          {titleNode ?? title}
        </h2>
        {intro ? (
          <p
            className={cn(
              "col-span-12 max-w-[62ch] text-[15px] leading-relaxed text-fg-muted md:col-span-9",
              right && "md:col-start-4 md:ml-auto md:text-right",
            )}
          >
            {intro}
          </p>
        ) : null}
      </div>

      <div className={cn("mt-12", right && "flex flex-col items-end")}>{children}</div>
    </motion.section>
  );
}
