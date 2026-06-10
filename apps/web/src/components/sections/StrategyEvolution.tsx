"use client";

import { motion } from "motion/react";
import { useTranslations } from "next-intl";
import { ArrowRight, RotateCcw } from "lucide-react";

import { fadeUp, gridStagger } from "@/lib/motion";

/**
 * 04 — 策略自进化。把「LLM 写代码 → 三道沙盒关 → 多目标 fitness → 变异循环」
 * 做成可见的流水线。代码 / fitness / 关卡是 D2 临床面 → 等宽精确、零神秘。
 */
const GATES = ["ast", "subprocess", "contract"] as const;

export function StrategyEvolution() {
  const t = useTranslations("evolution");

  return (
    <section className="group relative isolate overflow-hidden">
      <span
        aria-hidden
        className="pointer-events-none absolute -right-2 -top-16 -z-10 select-none font-display italic leading-none text-fg/[0.04] transition-colors duration-500 group-hover:text-gold/25"
        style={{ fontSize: "clamp(8rem, 24vw, 22rem)" }}
      >
        04
      </span>
      {/* dateline */}
      <div className="border-y border-fg/15">
        <div className="flex items-center gap-2.5 py-3 font-mono text-[11px] uppercase tracking-[0.22em] text-fg-muted">
          <span className="inline-block h-3 w-[2px] bg-seal/70" aria-hidden />
          <span>Evolution · written, sandboxed, mutated</span>
        </div>
      </div>

      <motion.h2
        initial={{ opacity: 0, y: 16 }}
        whileInView={{ opacity: 1, y: 0 }}
        viewport={{ once: true, margin: "-100px" }}
        transition={{ duration: 0.6 }}
        className="display-italic mt-12 max-w-[20ch] text-fg md:mt-16"
        style={{ fontSize: "clamp(2.25rem, 4.6vw, 3.6rem)", lineHeight: 1.0 }}
      >
        {t("title")}
      </motion.h2>
      <motion.p
        initial={{ opacity: 0, y: 16 }}
        whileInView={{ opacity: 1, y: 0 }}
        viewport={{ once: true, margin: "-100px" }}
        transition={{ duration: 0.6, delay: 0.1 }}
        className="mt-6 max-w-[60ch] text-[15.5px] leading-relaxed text-fg-muted"
      >
        {t("body")}
      </motion.p>

      {/* 流水线 */}
      <motion.div
        initial="hidden"
        whileInView="visible"
        viewport={{ once: true, margin: "-80px" }}
        variants={gridStagger}
        className="mt-14"
      >
        {/* 写代码 */}
        <motion.div variants={fadeUp} className="rounded-md border border-border-subtle bg-bg-elev p-5">
          <div className="font-mono text-[10.5px] uppercase tracking-[0.18em] text-fg-muted/60">
            {t("writeLabel")}
          </div>
          <code className="mt-2 block font-mono text-[13.5px] text-cyan">
            {t("writeCode")}
          </code>
        </motion.div>

        {/* 三道沙盒关 */}
        <motion.div variants={fadeUp} className="mt-6 flex items-center gap-2 font-mono text-[11px] uppercase tracking-[0.16em] text-seal">
          <span className="inline-block h-2 w-2 rotate-45 bg-seal/80" aria-hidden />
          {t("gatesLabel")}
        </motion.div>
        <div className="mt-4 grid gap-px bg-fg/10 md:grid-cols-3">
          {GATES.map((g, i) => (
            <motion.div
              key={g}
              variants={fadeUp}
              className="bg-bg p-5 transition-colors duration-300 hover:bg-bg-deep/50"
            >
              <div className="flex items-center gap-2 font-mono text-[12px] text-fg">
                <span className="text-seal/70">{i + 1}</span>
                {t(`gates.${g}`)}
              </div>
              <p className="mt-1.5 text-[13px] leading-snug text-fg-muted/80">
                {t(`gates.${g}Note`)}
              </p>
            </motion.div>
          ))}
        </div>

        {/* fitness + 变异循环 */}
        <motion.div
          variants={fadeUp}
          className="mt-6 flex flex-col gap-4 rounded-md border border-cyan/30 bg-cyan/[0.04] p-5 md:flex-row md:items-center md:justify-between"
        >
          <div>
            <div className="font-mono text-[10.5px] uppercase tracking-[0.18em] text-cyan">
              {t("fitnessLabel")}
            </div>
            <code className="mt-1.5 block font-mono text-[13px] text-fg">
              {t("fitness")}
            </code>
          </div>
          <div className="flex shrink-0 items-center gap-2 font-mono text-[11.5px] text-fg-muted">
            <RotateCcw className="size-3.5 text-seal/70" aria-hidden />
            {t("loop")}
            <ArrowRight className="size-3.5 text-seal/40" aria-hidden />
          </div>
        </motion.div>
      </motion.div>
    </section>
  );
}
