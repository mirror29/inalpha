"use client";

import { Check, X } from "lucide-react";
import { motion } from "motion/react";
import { useTranslations } from "next-intl";

import { BroadsheetSection } from "@/components/primitives/BroadsheetSection";
import { fadeUp, stagger } from "@/lib/motion";

const ROWS = ["lineage", "replay", "guardrail"] as const;

/**
 * 01 — The problem. Split-screen 视觉对峙：3 回合，每回合左红右青对置。
 *
 *   ─ 01 / LINEAGE · 血缘 ───────────  black box  vs  inalpha  ──
 *   ╔═══════════════════════════╦═══════════════════════════════╗
 *   ║ 01                        ║ 01                            ║
 *   ║ ✗  「做多 BTC 0.62」     ║ ✓  research_id → backtest_…  ║
 *   ║    源数据/prompt/模型版本   ║    全链 UUID，任意点反查       ║
 *   ║ [fox-red glow]            ║ [cyan glow]                   ║
 *   ╚═══════════════════════════╩═══════════════════════════════╝
 *
 * 设计要点：
 *   - 每行独立 article，自带 eyebrow + 两半 split panel
 *   - 大数字 `01/02/03` display-italic（跟 Hero 同字体）作视觉重锚
 *   - 左半 fox-red 径向渐变 + ✗ 大图标；右半 cyan 径向渐变 + ✓ 大图标
 *   - 中央一根 fox-red→cyan 渐变竖线，象征"对峙"
 *   - hover：accent 强化（边线饱和度 + 渐变更深 + 图标 scale），3 处同时响应
 *   - 移动端塌成上下堆叠，中央竖线变成横分隔条
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
        className="space-y-6"
      >
        {ROWS.map((row, idx) => {
          const num = String(idx + 1).padStart(2, "0");
          return (
            <motion.article
              key={row}
              variants={fadeUp}
              className="group relative overflow-hidden rounded-md border border-fg/10 bg-bg-deep/40"
            >
              {/* eyebrow ribbon */}
              <header className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-2 border-b border-fg/10 bg-bg-deep/60 px-5 py-3">
                <div className="flex items-baseline gap-3">
                  <span className="font-mono text-[11px] uppercase tracking-[0.32em] text-fg-muted/60">
                    {num}
                  </span>
                  <span className="font-mono text-[13px] uppercase tracking-[0.22em] text-fg">
                    {t(`table.${row}.want`)}
                  </span>
                </div>
                <div className="flex items-center gap-2 font-mono text-[10px] uppercase tracking-[0.26em]">
                  <span className="text-fox-red/75">black&nbsp;box</span>
                  <span className="text-fg-muted/30">vs</span>
                  <span className="text-cyan/85">inalpha</span>
                </div>
              </header>

              {/* split body */}
              <div className="relative grid grid-cols-1 md:grid-cols-2">
                {/* 左 · 黑盒（fox-red glow） */}
                <div className="relative overflow-hidden border-b border-fg/10 px-6 py-7 md:border-b-0 md:border-r md:border-fg/10 md:px-8 md:py-9">
                  {/* fox-red radial glow */}
                  <div
                    aria-hidden
                    className="pointer-events-none absolute inset-0 transition-opacity duration-300 group-hover:opacity-100"
                    style={{
                      background:
                        "radial-gradient(ellipse at 0% 100%, rgba(200,70,60,0.18), rgba(200,70,60,0.06) 45%, transparent 78%)",
                      opacity: 0.7,
                    }}
                  />
                  <div className="relative flex items-start gap-5">
                    {/* 大数字 + 图标列 */}
                    <div className="flex flex-col items-start gap-3 shrink-0">
                      <span
                        className="display-italic leading-none text-fox-red/30 transition-colors group-hover:text-fox-red/55"
                        style={{
                          fontSize: "clamp(2.75rem, 5.5vw, 4.25rem)",
                          fontWeight: 300,
                        }}
                      >
                        {num}
                      </span>
                      <span className="inline-flex size-7 items-center justify-center rounded-full border border-fox-red/40 bg-fox-red/10 text-fox-red transition-all group-hover:scale-110 group-hover:border-fox-red/85">
                        <X className="size-4 stroke-[2.5]" />
                      </span>
                    </div>
                    {/* 内容 */}
                    <div className="min-w-0 flex-1">
                      <p className="font-mono text-[10px] uppercase tracking-[0.28em] text-fox-red/65">
                        Black box LLM
                      </p>
                      <p className="mt-3 font-mono text-[15.5px] italic leading-relaxed text-fg-muted/90">
                        {t(`table.${row}.blackbox`)}
                      </p>
                    </div>
                  </div>
                </div>

                {/* 中央竖线（仅桌面） + vs glyph */}
                <div
                  aria-hidden
                  className="pointer-events-none absolute left-1/2 top-0 hidden h-full w-px -translate-x-1/2 md:block"
                  style={{
                    background:
                      "linear-gradient(to bottom, transparent, rgba(200,70,60,0.4), rgba(95,179,255,0.4), transparent)",
                  }}
                />

                {/* 右 · Inalpha（cyan glow） */}
                <div className="relative overflow-hidden px-6 py-7 md:px-8 md:py-9">
                  {/* cyan radial glow */}
                  <div
                    aria-hidden
                    className="pointer-events-none absolute inset-0 transition-opacity duration-300 group-hover:opacity-100"
                    style={{
                      background:
                        "radial-gradient(ellipse at 100% 0%, rgba(95,179,255,0.20), rgba(95,179,255,0.07) 45%, transparent 78%)",
                      opacity: 0.7,
                    }}
                  />
                  <div className="relative flex items-start gap-5">
                    <div className="flex flex-col items-start gap-3 shrink-0">
                      <span
                        className="display-italic leading-none text-cyan/35 transition-colors group-hover:text-cyan/70"
                        style={{
                          fontSize: "clamp(2.75rem, 5.5vw, 4.25rem)",
                          fontWeight: 300,
                        }}
                      >
                        {num}
                      </span>
                      <span className="inline-flex size-7 items-center justify-center rounded-full border border-cyan/45 bg-cyan/10 text-cyan transition-all group-hover:scale-110 group-hover:border-cyan/90">
                        <Check className="size-4 stroke-[2.5]" />
                      </span>
                    </div>
                    <div className="min-w-0 flex-1">
                      <p className="font-mono text-[10px] uppercase tracking-[0.28em] text-cyan/75">
                        Inalpha
                      </p>
                      <p className="mt-3 text-[15.5px] leading-relaxed text-fg">
                        {t(`table.${row}.inalpha`)}
                      </p>
                    </div>
                  </div>
                </div>
              </div>
            </motion.article>
          );
        })}
      </motion.div>
    </BroadsheetSection>
  );
}
