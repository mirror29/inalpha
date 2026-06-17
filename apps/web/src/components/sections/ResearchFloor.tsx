"use client";

import * as React from "react";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import { useTranslations } from "next-intl";

const LEGENDS = ["Buffett", "Lynch", "Wood", "Burry", "Druckenmiller", "Marks"];

type Side = "bull" | "bear" | "legend" | "risk";
type Turn = { speaker: string; side: Side; text: string; re?: string };

/** side → 颜色（看多绿 / 看空红 / 大师朱 / 风控金）。 */
const TONE: Record<Side, string> = {
  bull: "text-bull",
  bear: "text-fox-red",
  legend: "text-seal",
  risk: "text-gold",
};

/** side → 发言标记（多▲ / 空▼ / 大师◆ / 风控■），与左右站位一起构成对垒感。 */
const MARK: Record<Side, string> = {
  bull: "▲",
  bear: "▼",
  legend: "◆",
  risk: "■",
};

/**
 * 03 — 研究地基。序号 + 终端辩论框在左，标题 / 大师团在右。
 * 辩论框做成终端里的对垒：多头靠左（▲绿）、空头靠右缩进（▼红）逐条互相反驳（↩ re:），
 * 标题栏下一条多空力量条随发言此消彼长；大师 / 风控以分隔线收束，
 * 最后综合成 decision_record，循环播放（结尾多停 2 拍便于读结论）。
 * reduced-motion 下静态全显。示例，非投资建议。
 */
export function ResearchFloor() {
  const t = useTranslations("research");
  const reduce = useReducedMotion();
  const debate = t.raw("debate") as Turn[];
  const TOTAL = debate.length + 1;

  const [step, setStep] = React.useState(0);
  React.useEffect(() => {
    if (reduce) {
      setStep(TOTAL);
      return;
    }
    const id = setInterval(() => setStep((s) => (s >= TOTAL + 2 ? 0 : s + 1)), 1050);
    return () => clearInterval(id);
  }, [reduce, TOTAL]);

  const shown = Math.min(step, debate.length);
  const verdictOn = step > debate.length;
  const playing = step < TOTAL;

  const visible = debate.slice(0, shown);
  const bulls = visible.filter((m) => m.side === "bull").length;
  const bears = visible.filter((m) => m.side === "bear").length;
  const bullPct = bulls + bears === 0 ? 50 : (bulls / (bulls + bears)) * 100;

  return (
    <section className="group relative isolate overflow-hidden">
      <span
        aria-hidden
        className="pointer-events-none absolute -left-2 -top-16 -z-10 select-none font-display italic leading-none text-fg/[0.04] transition-colors duration-500 group-hover:text-gold/25"
        style={{ fontSize: "clamp(8rem, 24vw, 22rem)" }}
      >
        03
      </span>

      <div className="border-y border-fg/15">
        <div className="flex items-center gap-2.5 py-3 font-mono text-[11px] uppercase tracking-[0.22em] text-fg-muted">
          <span className="inline-block h-3 w-[2px] bg-seal/70" aria-hidden />
          <span>Research · opposing minds, one synthesis</span>
        </div>
      </div>

      <div className="grid grid-cols-12 gap-x-8 gap-y-10 pt-12 md:pt-16">
        {/* 左：终端辩论框 */}
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true, margin: "-100px" }}
          transition={{ duration: 0.6 }}
          className="order-2 col-span-12 md:order-1 md:col-span-7"
        >
          <div className="overflow-hidden rounded-md border border-border-subtle bg-bg-deep font-mono">
            {/* 窗口标题栏 */}
            <div className="flex items-center gap-3 border-b border-border-subtle px-4 py-2.5 text-[10.5px] uppercase tracking-[0.18em] text-fg-muted/70">
              <span className="flex gap-1.5" aria-hidden>
                <span className="size-2 rounded-full bg-fox-red/70" />
                <span className="size-2 rounded-full bg-gold/70" />
                <span className="size-2 rounded-full bg-bull/70" />
              </span>
              <span>{t("transcriptLabel")}</span>
              <span className="ml-auto text-fg-muted/40">{t("transcriptHint")}</span>
            </div>

            {/* 多空力量条：随发言此消彼长 */}
            <div className="flex items-center gap-3 border-b border-border-subtle px-4 py-2 text-[9.5px] uppercase tracking-[0.18em]">
              <span className="tabular-nums text-bull">
                {MARK.bull} {t("tallyBull")} {bulls}
              </span>
              <div className="flex h-[3px] flex-1 overflow-hidden rounded-full bg-fg/10" aria-hidden>
                <span
                  className="bg-bull/80 transition-[width] duration-500 ease-out"
                  style={{ width: `${bullPct}%` }}
                />
                <span
                  className="flex-1 bg-fox-red/80 transition-[width] duration-500 ease-out"
                  style={{ width: `${100 - bullPct}%` }}
                />
              </div>
              <span className="tabular-nums text-fox-red">
                {bears} {t("tallyBear")} {MARK.bear}
              </span>
            </div>

            {/* 终端正文：多头靠左、空头靠右缩进，逐条互相反驳 */}
            <div className="flex min-h-[21rem] flex-col gap-y-2 p-4 text-[12.5px] leading-relaxed sm:min-h-[22rem]">
              <AnimatePresence mode="popLayout">
                {visible.map((m, i) => {
                  const bear = m.side === "bear";
                  const panel = m.side === "legend" || m.side === "risk";
                  return (
                    <motion.div
                      key={`${i}-${m.speaker}`}
                      layout
                      initial={{ opacity: 0, x: bear ? 12 : panel ? 0 : -12 }}
                      animate={{ opacity: 1, x: 0 }}
                      /* exit 只发生在循环重置（全部消息同时退出），popLayout 会把退出项
                         绝对定位叠在新一轮消息上 —— 必须瞬时移除，否则整框文字重影 */
                      exit={{ opacity: 0, transition: { duration: 0 } }}
                      transition={{ duration: 0.28 }}
                      className={
                        panel
                          ? "mt-1 flex w-full flex-wrap items-baseline gap-x-2 border-t border-dashed border-border-subtle pt-2.5"
                          : "flex max-w-[88%] flex-wrap items-baseline gap-x-2 " +
                            (bear ? "self-end justify-end text-right" : "self-start")
                      }
                    >
                      {!bear ? <span className={panel ? "text-seal/60" : "text-bull/60"}>&gt;</span> : null}
                      <span className={TONE[m.side]}>
                        {MARK[m.side]} {m.speaker}
                      </span>
                      {m.re ? (
                        <span className="text-[9px] uppercase tracking-[0.12em] text-fg-muted/50">
                          ↩ {m.re}
                        </span>
                      ) : (
                        <span className={"text-[9px] uppercase tracking-[0.12em] opacity-70 " + TONE[m.side]}>
                          [{m.side}]
                        </span>
                      )}
                      <span className="text-fg-muted">{m.text}</span>
                      {bear ? <span className="text-fox-red/60">&lt;</span> : null}
                    </motion.div>
                  );
                })}
              </AnimatePresence>

              <AnimatePresence>
                {verdictOn ? (
                  <motion.div
                    key="verdict"
                    initial={{ opacity: 0, y: 6 }}
                    animate={{ opacity: 1, y: 0 }}
                    exit={{ opacity: 0 }}
                    transition={{ duration: 0.35 }}
                    className="mt-3 flex flex-wrap items-baseline gap-x-3 border-t border-border-subtle pt-3"
                  >
                    <span className="text-cyan">↳ {t("verdict")}</span>
                    <span className="tabular-nums text-fg">{t("verdictText")}</span>
                  </motion.div>
                ) : null}
              </AnimatePresence>

              {playing ? (
                <span
                  className={
                    "caret-blink inline-block text-cyan " +
                    (debate[shown]?.side === "bear" ? "self-end" : "self-start")
                  }
                  aria-hidden
                >
                  ▋
                </span>
              ) : null}
            </div>
          </div>
        </motion.div>

        {/* 右：标题 + 主张 + 大师团 */}
        <div className="order-1 col-span-12 md:order-2 md:col-span-5">
          <motion.h2
            initial={{ opacity: 0, y: 16 }}
            whileInView={{ opacity: 1, y: 0 }}
            viewport={{ once: true, margin: "-100px" }}
            transition={{ duration: 0.6 }}
            className="display-italic text-fg"
            style={{ fontSize: "clamp(2.25rem, 4.4vw, 3.4rem)", lineHeight: 1.02 }}
          >
            {t("title")}
          </motion.h2>
          <motion.p
            initial={{ opacity: 0, y: 16 }}
            whileInView={{ opacity: 1, y: 0 }}
            viewport={{ once: true, margin: "-100px" }}
            transition={{ duration: 0.6, delay: 0.1 }}
            className="mt-6 max-w-[44ch] text-[15.5px] leading-relaxed text-fg-muted"
          >
            {t("body")}
          </motion.p>
          <div className="mt-8">
            <span className="font-mono text-[10px] uppercase tracking-[0.24em] text-fg-muted/60">
              {t("legendsLabel")}
            </span>
            <div className="mt-3 flex flex-wrap gap-1.5">
              {LEGENDS.map((n) => (
                <span
                  key={n}
                  className="rounded-sm border border-border-subtle px-2 py-1 font-mono text-[11px] text-fg-muted transition-colors hover:border-seal/60 hover:text-fg"
                >
                  {n}
                </span>
              ))}
            </div>
          </div>

          {/* Agent skills —— 投研方法论即插即用，按需读取 */}
          <motion.div
            initial={{ opacity: 0, y: 16 }}
            whileInView={{ opacity: 1, y: 0 }}
            viewport={{ once: true, margin: "-100px" }}
            transition={{ duration: 0.6, delay: 0.15 }}
            className="mt-8 border-l-2 border-seal/40 pl-4"
          >
            <span className="font-mono text-[10px] uppercase tracking-[0.24em] text-seal/90">
              {t("skills.label")}
            </span>
            <p className="mt-2 max-w-[44ch] text-[13.5px] leading-relaxed text-fg-muted">
              {t("skills.body")}
            </p>
          </motion.div>
        </div>
      </div>
    </section>
  );
}
