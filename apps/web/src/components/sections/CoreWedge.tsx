"use client";

import { motion } from "motion/react";
import { useTranslations } from "next-intl";
import { Check, X } from "lucide-react";

import { EASE_OUT_QUART, fadeUp, gridStagger } from "@/lib/motion";

/** 大卡片 hover 只抬升不缩放 —— 卡片横向占满栅格，scale 放大会让
 *  左右边框越界被 section 的 overflow-hidden 裁掉。 */
const cardLift = {
  y: -4,
  transition: { duration: 0.2, ease: EASE_OUT_QUART },
} as const;

/**
 * 01 — 核心楔子。门面第一屏：把差异化做成可见的对位器物——
 * 「黑盒」（暗底 / 朱红 / 涂黑问号 / ✕ 信息缺失）⇄「账本」（亮底 / 青 / 行号 / ✓ 逐行留痕）。
 * 三组人话对照（亏钱时 / 赚钱时 / 谁能下单），大白话不堆技术黑话。
 */
const ROWS = ["lose", "win", "trigger"] as const;

export function CoreWedge() {
  const t = useTranslations("wedge");

  return (
    <section className="group relative isolate overflow-hidden">
      <span
        aria-hidden
        className="pointer-events-none absolute -right-2 -top-16 -z-10 select-none font-display italic leading-none text-fg/[0.04] transition-colors duration-500 group-hover:text-gold/25"
        style={{ fontSize: "clamp(8rem, 24vw, 22rem)" }}
      >
        01
      </span>

      {/* dateline */}
      <div className="border-y border-fg/15">
        <div className="flex items-center gap-2.5 py-3 font-mono text-[11px] uppercase tracking-[0.22em] text-fg-muted">
          <span className="inline-block h-3 w-[2px] bg-seal/70" aria-hidden />
          <span>Why Inalpha · the black box vs the ledger</span>
        </div>
      </div>

      {/* 大字主张 */}
      <motion.h2
        initial="hidden"
        whileInView="visible"
        viewport={{ once: true, margin: "-100px" }}
        variants={gridStagger}
        className="display-italic mt-12 max-w-[20ch] md:mt-16"
        style={{ fontSize: "clamp(2.25rem, 5vw, 4rem)", lineHeight: 1.0 }}
      >
        <motion.span variants={fadeUp} className="block text-fg-muted/70">
          {t("title")}
        </motion.span>
        <motion.span variants={fadeUp} className="block text-seal">
          {t("titleAlt")}
        </motion.span>
      </motion.h2>

      {/* 对位器物：黑盒 ⇄ 账本 */}
      <div className="mt-14 grid gap-4 md:grid-cols-2">
        {/* ── 黑盒 ── */}
        <motion.div
          initial="hidden"
          whileInView="visible"
          viewport={{ once: true, margin: "-80px" }}
          variants={gridStagger}
          whileHover={cardLift}
          className="group/card relative overflow-hidden rounded-md border border-fox-red/20 bg-bg-deep p-6 transition-[border-color,box-shadow] duration-300 hover:border-fox-red/45 hover:shadow-[0_10px_44px_-14px_color-mix(in_oklab,var(--down)_30%,transparent)] md:p-8"
        >
          <span
            aria-hidden
            className="pointer-events-none absolute -right-3 -top-8 select-none font-display italic leading-none text-fox-red/[0.07] transition-colors duration-500 group-hover/card:text-fox-red/[0.14]"
            style={{ fontSize: "clamp(6rem, 14vw, 11rem)" }}
          >
            ?
          </span>
          <div className="relative mb-5 flex items-center gap-2 font-mono text-[11px] uppercase tracking-[0.2em] text-fox-red/80">
            <X className="size-3.5" strokeWidth={2.5} />
            {t("blackboxLabel")}
          </div>
          <div className="relative divide-y divide-fg/8">
            {ROWS.map((key) => (
              <motion.div key={key} variants={fadeUp} className="py-4 first:pt-0 last:pb-0">
                <div className="font-mono text-[10px] uppercase tracking-[0.16em] text-fox-red/50">
                  {t(`rows.${key}.trait`)}
                </div>
                <p className="mt-1.5 text-[14px] leading-relaxed text-fg-muted/55">
                  {t(`rows.${key}.blackbox`)}
                </p>
              </motion.div>
            ))}
          </div>
        </motion.div>

        {/* ── 账本 ── */}
        <motion.div
          initial="hidden"
          whileInView="visible"
          viewport={{ once: true, margin: "-80px" }}
          variants={gridStagger}
          whileHover={cardLift}
          className="group/card relative overflow-hidden rounded-md border border-cyan/30 bg-bg-elev p-6 transition-[border-color,box-shadow] duration-300 hover:border-cyan/60 hover:shadow-[0_10px_44px_-14px_color-mix(in_oklab,var(--accent)_35%,transparent)] md:p-8"
        >
          <div className="relative mb-5 flex items-center gap-2 font-mono text-[11px] uppercase tracking-[0.2em] text-cyan">
            <Check className="size-3.5" strokeWidth={2.5} />
            {t("inalphaLabel")} · ledger
          </div>
          <div className="relative divide-y divide-fg/8">
            {ROWS.map((key, i) => (
              <motion.div
                key={key}
                variants={fadeUp}
                className="flex gap-4 py-4 first:pt-0 last:pb-0"
              >
                <span className="mt-0.5 shrink-0 font-mono text-[11px] tabular-nums text-cyan/50 transition-colors duration-300 group-hover/card:text-cyan/85">
                  {String(i + 1).padStart(2, "0")}
                </span>
                <div className="min-w-0">
                  <div className="flex items-center gap-1.5 font-mono text-[10px] uppercase tracking-[0.16em] text-cyan/80">
                    <Check className="size-3" strokeWidth={3} />
                    {t(`rows.${key}.trait`)}
                  </div>
                  <p className="mt-1.5 text-[14px] leading-relaxed text-fg">
                    {t(`rows.${key}.inalpha`)}
                  </p>
                </div>
              </motion.div>
            ))}
          </div>
        </motion.div>
      </div>

      {/* 收口 */}
      <motion.p
        initial={{ opacity: 0, y: 16 }}
        whileInView={{ opacity: 1, y: 0 }}
        viewport={{ once: true, margin: "-80px" }}
        transition={{ duration: 0.6 }}
        className="display-italic mt-12 max-w-[30ch] text-[clamp(1.4rem,2.6vw,2.1rem)] leading-snug text-fg"
      >
        {t("close")}
      </motion.p>
    </section>
  );
}
