"use client";

import { motion } from "motion/react";
import { useTranslations } from "next-intl";

import { BroadsheetSection } from "@/components/primitives/BroadsheetSection";
import { fadeUp, stagger } from "@/lib/motion";

const ROWS = ["signal", "reason", "replay"] as const;

/**
 * 01 — The problem. Side-by-side comparison: what you want · black-box LLM · Inalpha.
 * Hairline grid, no card chrome — feels like a printed comparison table.
 */
export function BlackBoxProblem() {
  const t = useTranslations("problem");

  return (
    <BroadsheetSection
      index="01"
      eyebrow={t("eyebrow")}
      title=""
      titleNode={
        <>
          {t("title")}
          <br />
          <span className="text-fox-red/85">{t("titleAlt")}</span>
        </>
      }
      intro={t("sub")}
    >
      <motion.div
        initial="hidden"
        whileInView="visible"
        viewport={{ once: true, margin: "-80px" }}
        variants={stagger}
        className="overflow-hidden border border-fg/12"
      >
        {/* Header row */}
        <motion.div
          variants={fadeUp}
          className="grid grid-cols-12 border-b border-fg/12 font-mono text-[10px] uppercase tracking-[0.24em] text-fg-muted/70"
        >
          <div className="col-span-3 p-4">{t("table.header.want")}</div>
          <div className="col-span-4 border-l border-fg/12 p-4">
            {t("table.header.blackbox")}
          </div>
          <div className="col-span-5 border-l border-fg/12 p-4 text-cyan">
            {t("table.header.inalpha")}
          </div>
        </motion.div>

        {/* Body rows */}
        {ROWS.map((row, idx) => (
          <motion.div
            key={row}
            variants={fadeUp}
            className={`grid grid-cols-12 ${
              idx < ROWS.length - 1 ? "border-b border-fg/12" : ""
            }`}
          >
            <div className="col-span-3 p-4 font-mono text-[13px] uppercase tracking-[0.16em] text-fg">
              {t(`table.${row}.want`)}
            </div>
            <div className="col-span-4 border-l border-fg/12 p-4 text-[14px] leading-relaxed text-fg-muted/80">
              <span className="font-mono italic">{t(`table.${row}.blackbox`)}</span>
            </div>
            <div className="col-span-5 border-l border-fg/12 p-4 text-[14px] leading-relaxed text-fg">
              {t(`table.${row}.inalpha`)}
            </div>
          </motion.div>
        ))}
      </motion.div>
    </BroadsheetSection>
  );
}
