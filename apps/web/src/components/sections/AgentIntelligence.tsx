"use client";

import * as React from "react";
import { motion, useReducedMotion } from "motion/react";
import { useTranslations } from "next-intl";

import { Foxfire } from "@/components/primitives/Foxfire";
import { fadeUp, gridStagger } from "@/lib/motion";

/**
 * 02 — Factor timing（头牌差异点）。
 *
 * 把功能做成可见的器物：右侧「因子排名面板」按滚动 Rank IC 实时微动并自动重排，
 * 当前有效项点亮置顶 —— 直接演示「挑出当下最灵的因子」。motion `layout` 让每行
 * 平滑滑到新位次；reduced-motion 下静态。面板是 D2 第④面 → 等宽精确、零神秘，
 * 数值为示例（illustrative），只表达 factor.timing 的输出形态。
 */

/** `ic` = 60d 滚动 Rank IC（基线）；`recent` = rank_ic_recent（近窗，演示衰减巡检）。 */
type Factor = { name: string; ic: number; recent: number };

const INITIAL: Factor[] = [
  { name: "momentum_60d", ic: 0.082, recent: 0.071 },
  { name: "residual_reversal", ic: 0.061, recent: 0.066 },
  { name: "vol_carry", ic: 0.047, recent: 0.039 },
  { name: "amihud_illiq", ic: 0.024, recent: 0.021 },
  { name: "pead_drift", ic: 0.012, recent: 0.018 },
  { name: "value_bm", ic: -0.007, recent: -0.012 },
];
const IC_MAX = 0.09;
const IC_MIN = -0.02;

const clampIc = (v: number) => Math.min(IC_MAX, Math.max(IC_MIN, v));

/** 小幅随机游走 + 重排（每帧只动一点，像行情里因子有效性缓慢变化）。
 *  base 与 recent 各自独立游走 —— recent 跌破 base 即「衰减」。 */
function step(prev: Factor[]): Factor[] {
  return prev
    .map((f) => ({
      ...f,
      ic: clampIc(f.ic + (Math.random() - 0.5) * 0.014),
      recent: clampIc(f.recent + (Math.random() - 0.5) * 0.018),
    }))
    .sort((a, b) => b.ic - a.ic);
}

export function AgentIntelligence() {
  const t = useTranslations("intelligence");
  const reduce = useReducedMotion();
  const [factors, setFactors] = React.useState<Factor[]>(INITIAL);

  React.useEffect(() => {
    if (reduce) return;
    const id = setInterval(() => setFactors((p) => step(p)), 2200);
    return () => clearInterval(id);
  }, [reduce]);

  return (
    <section className="group relative overflow-hidden">
      {/* 破格巨字 —— factor.timing 作背景纹 */}
      <span
        aria-hidden
        className="pointer-events-none absolute -right-6 -top-6 select-none font-mono font-medium lowercase leading-none tracking-tighter text-fg/[0.035] transition-colors duration-500 group-hover:text-gold/20"
        style={{ fontSize: "clamp(5rem, 16vw, 15rem)" }}
      >
        factor.timing
      </span>

      {/* dateline */}
      <div className="relative border-y border-fg/15">
        <div className="flex items-center gap-2.5 py-3 font-mono text-[11px] uppercase tracking-[0.22em] text-fg-muted">
          <span className="inline-block h-3 w-[2px] bg-seal/70" aria-hidden />
          <span>Intelligence · factors that work now</span>
        </div>
      </div>

      <div className="relative grid grid-cols-12 gap-x-8 gap-y-12 pt-12 md:pt-16">
        {/* 左：巨型编辑标题 + 主张 */}
        <motion.div
          initial="hidden"
          whileInView="visible"
          viewport={{ once: true, margin: "-100px" }}
          variants={gridStagger}
          className="col-span-12 md:col-span-5"
        >
          <motion.h2
            variants={fadeUp}
            className="display-italic text-fg"
            style={{ fontSize: "clamp(2.25rem, 4.6vw, 3.6rem)", lineHeight: 1.0 }}
          >
            {t("title")}
          </motion.h2>
          <motion.p
            variants={fadeUp}
            className="mt-7 max-w-[42ch] text-[15.5px] leading-relaxed text-fg-muted"
          >
            {t("timing.body")}
          </motion.p>
          <motion.div
            variants={fadeUp}
            className="mt-8 inline-flex items-center gap-2.5 border-l-2 border-cyan pl-3 font-mono text-[12px] tracking-wide text-cyan"
          >
            {t("timing.caption")}
          </motion.div>

          {/* 因子库治理事实 —— D2 临床面，等宽精确 */}
          <motion.div variants={fadeUp} className="mt-9">
            <div className="font-mono text-[10.5px] uppercase tracking-[0.2em] text-fg-muted/60">
              ── {t("lib.heading")}
            </div>
            <ul className="mt-3 space-y-2">
              {(t.raw("lib.facts") as string[]).map((fact) => (
                <li
                  key={fact}
                  className="flex items-baseline gap-2.5 font-mono text-[12px] leading-relaxed text-fg-muted"
                >
                  <span className="mt-px inline-block size-1 shrink-0 translate-y-[0.4em] rounded-full bg-seal/70" aria-hidden />
                  <span>{fact}</span>
                </li>
              ))}
            </ul>
          </motion.div>
        </motion.div>

        {/* 右：因子排名面板（自动重排器物） */}
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true, margin: "-100px" }}
          transition={{ duration: 0.6 }}
          className="relative col-span-12 md:col-span-7 md:translate-y-2"
        >
          <Foxfire sparks={[{ top: "-4%", right: "6%", size: 4 }]} />
          <div className="relative overflow-hidden rounded-md border border-border-subtle bg-bg-elev">
            <div className="flex items-center justify-between border-b border-border-subtle px-5 py-3 font-mono text-[10.5px] uppercase tracking-[0.18em] text-fg-muted/70">
              <span>factor.timing — ranked by 60d rolling Rank IC</span>
              <span className="text-fg-muted/40">live · illustrative</span>
            </div>

            <div className="py-1">
              {factors.map((f, i) => {
                const on = i < 3;
                const neg = f.ic < 0;
                const pct = Math.max(4, (Math.abs(f.ic) / IC_MAX) * 100);
                return (
                  <motion.div
                    key={f.name}
                    layout
                    transition={{ type: "spring", stiffness: 380, damping: 34 }}
                    className="grid grid-cols-[1fr_auto] items-center gap-4 px-5 py-2.5"
                  >
                    <div className="min-w-0">
                      <div className="flex items-center gap-2">
                        <span
                          className={
                            "truncate font-mono text-[13px] transition-colors " +
                            (on ? "text-fg" : "text-fg-muted/60")
                          }
                        >
                          {f.name}
                        </span>
                        {on ? (
                          <span className="shrink-0 rounded-sm bg-cyan/15 px-1.5 py-0.5 font-mono text-[9px] uppercase tracking-[0.16em] text-cyan">
                            effective
                          </span>
                        ) : null}
                      </div>
                      <div className="mt-1.5 h-1 w-full rounded-full bg-fg/5">
                        <div
                          className={
                            "h-full rounded-full transition-[width] duration-700 ease-out " +
                            (neg ? "bg-fox-red/50" : on ? "bg-cyan" : "bg-fg-muted/35")
                          }
                          style={{ width: `${pct}%` }}
                        />
                      </div>
                    </div>
                    <div className="flex flex-col items-end gap-0.5">
                      <span
                        className={
                          "font-mono text-[13px] tabular-nums transition-colors " +
                          (neg ? "text-fox-red/80" : on ? "text-cyan" : "text-fg-muted/60")
                        }
                      >
                        {f.ic >= 0 ? "+" : ""}
                        {f.ic.toFixed(3)}
                      </span>
                      {/* rank_ic_recent —— 近窗 IC，跌破基线即衰减（↘ 红），守住/抬升（↗ 绿） */}
                      <span
                        className={
                          "font-mono text-[10px] tabular-nums " +
                          (f.recent < f.ic ? "text-fox-red/55" : "text-bull/55")
                        }
                      >
                        {f.recent < f.ic ? "↘" : "↗"} {f.recent >= 0 ? "+" : ""}
                        {f.recent.toFixed(3)}
                      </span>
                    </div>
                  </motion.div>
                );
              })}
            </div>

            {/* 因子库治理脚注 —— 79 因子 / 去相关 / 衰减巡检（不转大小写：保 ρ 与 IC 原样） */}
            <div className="border-t border-border-subtle px-5 py-2.5 font-mono text-[10.5px] normal-case tracking-[0.08em] text-fg-muted/55">
              {t("lib.panelFoot")}
            </div>
          </div>
        </motion.div>
      </div>
    </section>
  );
}
