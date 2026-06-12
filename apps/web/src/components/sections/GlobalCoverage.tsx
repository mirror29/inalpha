"use client";

import * as React from "react";
import { motion, useReducedMotion } from "motion/react";
import { useTranslations } from "next-intl";
import { ArrowRight } from "lucide-react";

import { BroadsheetSection } from "@/components/primitives/BroadsheetSection";
import { fadeUp, stagger } from "@/lib/motion";

const MARKETS = [
  "crypto", "us", "cn", "hk", "jp", "kr",
  "au", "in", "uk", "de", "indices", "macro",
] as const;
const AGENTS = ["research", "factor", "risk", "execution"] as const;

/**
 * 07 — Coverage。把「同一 orchestrator 路由全市场，加一个 venue 全 agent 即用」
 * 做成路由演示：venue 自动轮转点亮（也可 hover）→ orchestrator → 所有 agent 跟着亮。
 * 下方现状透明块为 D2 临床面。GitHub 数字已挪到 CTAFooter（Get started 下）。
 */
export function GlobalCoverage() {
  const t = useTranslations("coverage");
  const reduce = useReducedMotion();
  const items = t.raw("currentState.items") as string[];

  const [active, setActive] = React.useState(0);
  const [pinned, setPinned] = React.useState<number | null>(null);
  React.useEffect(() => {
    if (reduce || pinned !== null) return;
    const id = setInterval(() => setActive((a) => (a + 1) % MARKETS.length), 1600);
    return () => clearInterval(id);
  }, [reduce, pinned]);
  const live = pinned ?? active;

  return (
    <BroadsheetSection
      index="07"
      eyebrow="Coverage · twelve markets, one kernel"
      title={t("title")}
      intro={t("sub")}
    >
      <div className="space-y-14">
        {/* 路由演示：venue → orchestrator → agents */}
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true, margin: "-80px" }}
          transition={{ duration: 0.5 }}
          className="grid items-center gap-4 md:grid-cols-[1fr_auto_auto_auto_1fr] md:gap-6"
        >
          {/* venues */}
          <div className="grid grid-cols-3 gap-1.5 sm:grid-cols-4 md:grid-cols-3">
            {MARKETS.map((m, i) => {
              const on = i === live;
              return (
                <button
                  key={m}
                  type="button"
                  onMouseEnter={() => setPinned(i)}
                  onMouseLeave={() => setPinned(null)}
                  className={
                    "rounded-sm border px-2 py-1.5 text-center font-mono text-[11px] uppercase tracking-[0.12em] transition-all duration-200 " +
                    (on
                      ? "border-cyan bg-cyan/15 text-cyan"
                      : "border-fg/15 text-fg-muted/70 hover:border-cyan/40 hover:text-fg")
                  }
                >
                  {t(`tags.${m}`)}
                </button>
              );
            })}
          </div>

          {/* arrow —— 移动端纵向流转 90° 朝下，保住 venue → orchestrator 的路由叙事 */}
          <ArrowRight className="mx-auto size-4 rotate-90 text-seal/70 md:rotate-0" aria-hidden />

          {/* orchestrator —— 移动端横排紧凑药丸，md 起恢复立柱比例 */}
          <div className="mx-auto flex items-center justify-center rounded-md border border-seal/40 bg-seal/[0.06] px-6 py-2.5 text-center md:px-4 md:py-6">
            <span className="font-mono text-[11px] uppercase tracking-[0.18em] text-seal">
              orchestrator
            </span>
          </div>

          {/* arrow */}
          <ArrowRight className="mx-auto size-4 rotate-90 text-seal/70 md:rotate-0" aria-hidden />

          {/* agents — venue 一变，全部闪一下（每个 agent 都拿到新 venue）
              移动端 2×2 收紧纵向空间，md 起恢复右列纵排 */}
          <div className="grid grid-cols-2 gap-1.5 md:grid-cols-1">
            {AGENTS.map((a) => (
              <motion.div
                key={`${a}-${live}`}
                initial={{ backgroundColor: "color-mix(in oklab, var(--accent) 16%, transparent)" }}
                animate={{ backgroundColor: "color-mix(in oklab, var(--accent) 0%, transparent)" }}
                transition={{ duration: 1.1, ease: "easeOut" }}
                className="flex items-center gap-2 rounded-sm border border-border-subtle px-3 py-1.5"
              >
                <span className="inline-block size-1.5 rounded-full bg-cyan" aria-hidden />
                <span className="font-mono text-[12px] text-fg">{a}</span>
              </motion.div>
            ))}
          </div>

          <p className="col-span-full mt-2 text-center font-mono text-[11px] uppercase tracking-[0.16em] text-fg-muted/60 md:col-span-full">
            add a venue — every agent gets it for free
          </p>
        </motion.div>

        {/* Current state — transparency callout */}
        <motion.div
          initial="hidden"
          whileInView="visible"
          viewport={{ once: true, margin: "-80px" }}
          variants={stagger}
          className="grid grid-cols-12 gap-6 border-l-2 border-gold/60 pl-6"
        >
          <motion.p
            variants={fadeUp}
            className="col-span-12 font-mono text-[11px] uppercase tracking-[0.28em] text-gold md:col-span-3"
          >
            ── {t("currentState.title")}
          </motion.p>
          <ul className="col-span-12 space-y-3 md:col-span-9">
            {items.map((item, idx) => (
              <motion.li
                key={item}
                variants={fadeUp}
                className="flex items-baseline gap-3 text-[14.5px] leading-relaxed text-fg-muted"
              >
                <span className="font-mono text-[10px] uppercase tracking-[0.22em] text-fg-muted/50">
                  {String(idx + 1).padStart(2, "0")}
                </span>
                <span>{item}</span>
              </motion.li>
            ))}
          </ul>
        </motion.div>
      </div>
    </BroadsheetSection>
  );
}
