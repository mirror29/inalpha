"use client";

import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import { useTranslations } from "next-intl";
import * as React from "react";

import { fadeUp, gridStagger } from "@/lib/motion";

/**
 * 06 — 信任边界（护城河）。把「LLM 够不到下单」做成关所盖章叙事：
 * agent → propose → approve → execute → 订单簿，两个请求光点错峰流动、
 * 在 approve 节点真实停顿落金色核验记号，轨道随行进分段点亮，每过一单
 * 滚动一行审计回执；下方 LLM 直连支路红点加速撞墙、✕ 闪烁抖动后弹回。
 * 流程 / 工具名 / 回执是 D2 临床面 → 等宽精确。
 */

/** 主管道：光点行进时长 / 周期间隔（两光点错峰半个周期）。 */
const PIPE_D = 4.4;
const PIPE_RD = 0.4;
const PERIOD = PIPE_D + PIPE_RD;
/** 光点抵达 approve（50% 处）的绝对时刻。 */
const APPROVE_AT = PIPE_D * 0.42;
/** 光点 keyframes：淡入 → 行至 approve 停顿 ~0.57s → 续行至订单簿淡出。 */
const DOT_LEFT = ["1%", "1%", "50%", "50%", "99%", "99%"];
const DOT_TIMES = [0, 0.04, 0.42, 0.55, 0.96, 1];

/** 回执行 id —— seq 派生的稳定伪随机 4 位 hex，纯装饰临床面。 */
function receiptId(seq: number): string {
  return (((seq + 11) * 2654435761) >>> 0).toString(16).padStart(8, "0").slice(0, 4);
}

/** 回执时刻 —— 从 10:42:07 起每单 +5s，确定性生成。 */
function receiptTime(seq: number): string {
  const total = 10 * 3600 + 42 * 60 + 7 + seq * 5;
  const p = (n: number) => String(n).padStart(2, "0");
  return `${p(Math.floor(total / 3600) % 24)}:${p(Math.floor((total % 3600) / 60))}:${p(total % 60)}`;
}
export function TrustBoundary() {
  const t = useTranslations("trust");
  const reduce = useReducedMotion();
  /** 审计回执：每半个周期过一单（两光点错峰），保留最近 3 行。 */
  const [receipts, setReceipts] = React.useState<{ seq: number }[]>([]);

  React.useEffect(() => {
    if (reduce) return;
    let seq = 0;
    const iv = setInterval(
      () => {
        seq += 1;
        setReceipts((rs) => [{ seq }, ...rs].slice(0, 3));
      },
      (PERIOD / 2) * 1000
    );
    return () => clearInterval(iv);
  }, [reduce]);

  const FLOW = [
    { key: "agent", tone: "muted", tool: t("agentLabel"), desc: "" },
    { key: "propose", tone: "cyan", tool: t("steps.propose.tool"), desc: t("steps.propose.label") },
    { key: "approve", tone: "cyan", tool: t("steps.approve.tool"), desc: t("steps.approve.label") },
    { key: "execute", tone: "cyan", tool: t("steps.execute.tool"), desc: t("steps.execute.label") },
    { key: "order", tone: "bull", tool: t("orderLabel"), desc: "" },
  ] as const;

  return (
    <section className="group relative isolate overflow-hidden">
      <span
        aria-hidden
        className="pointer-events-none absolute -right-2 -top-16 -z-10 select-none font-display italic leading-none text-fg/[0.04] transition-colors duration-500 group-hover:text-gold/25"
        style={{ fontSize: "clamp(8rem, 24vw, 22rem)" }}
      >
        06
      </span>
      {/* dateline */}
      <div className="border-y border-fg/15">
        <div className="flex items-center gap-2.5 py-3 font-mono text-[11px] uppercase tracking-[0.22em] text-fg-muted">
          <span className="inline-block h-3 w-[2px] bg-seal/70" aria-hidden />
          <span>Trust boundary · the moat</span>
        </div>
      </div>

      <div className="grid grid-cols-12 gap-x-8 gap-y-8 pt-12 md:pt-16">
        <motion.h2
          initial={{ opacity: 0, y: 16 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true, margin: "-100px" }}
          transition={{ duration: 0.6 }}
          className="display-italic col-span-12 text-fg md:col-span-7"
          style={{ fontSize: "clamp(2.25rem, 4.6vw, 3.6rem)", lineHeight: 1.0 }}
        >
          {t("title")}
          <br />
          <span className="text-seal">{t("titleAlt")}</span>
        </motion.h2>
        <motion.p
          initial={{ opacity: 0, y: 16 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true, margin: "-100px" }}
          transition={{ duration: 0.6, delay: 0.1 }}
          className="col-span-12 max-w-[52ch] self-end text-[15.5px] leading-relaxed text-fg-muted md:col-span-5"
        >
          {t("body")}
        </motion.p>
      </div>

      {/* 审批管道：节点等距落在渐变轨道上，青色请求平滑流向订单簿 */}
      <motion.div
        initial="hidden"
        whileInView="visible"
        viewport={{ once: true, margin: "-80px" }}
        variants={gridStagger}
        className="mt-16"
      >
        <div className="relative">
          {/* 轨道基线（暗）—— 点亮交给下面的行进轨迹；移动端管道转纵向，横轨隐藏 */}
          <div
            className="absolute left-0 right-0 top-[7px] hidden h-px md:block"
            style={{
              background:
                "linear-gradient(to right, color-mix(in oklab, var(--ink-muted) 35%, transparent), var(--accent), color-mix(in oklab, var(--bull) 70%, transparent))",
              opacity: 0.35,
            }}
            aria-hidden
          />
          {/* 移动端纵向轨道：沿节点圆点中心垂落 */}
          <div
            className="absolute bottom-2 left-[6px] top-2 w-px md:hidden"
            style={{
              background:
                "linear-gradient(to bottom, color-mix(in oklab, var(--ink-muted) 35%, transparent), var(--accent), color-mix(in oklab, var(--bull) 70%, transparent))",
              opacity: 0.35,
            }}
            aria-hidden
          />
          {/* 轨道点亮：跟随首个光点分段提亮，approve 停顿时一起停 */}
          <motion.div
            aria-hidden
            className="absolute left-0 right-0 top-[7px] hidden h-px origin-left md:block"
            style={{
              background:
                "linear-gradient(to right, color-mix(in oklab, var(--accent) 55%, transparent), var(--accent), var(--bull))",
            }}
            initial={{ scaleX: reduce ? 1 : 0, opacity: reduce ? 0.85 : 0 }}
            animate={
              reduce
                ? undefined
                : { scaleX: [0, 0, 0.5, 0.5, 1, 1], opacity: [0, 0.9, 0.9, 0.9, 0.9, 0] }
            }
            transition={
              reduce
                ? undefined
                : { duration: PIPE_D, times: DOT_TIMES, repeat: Infinity, repeatDelay: PIPE_RD }
            }
          />
          {/* 两个请求光点错峰流动（批量请求感），在 approve 停顿后续行 */}
          {!reduce
            ? [0, PERIOD / 2].map((delay) => (
                <motion.span
                  key={delay}
                  aria-hidden
                  className="absolute top-[7px] z-10 hidden size-2.5 -translate-y-1/2 rounded-full bg-cyan shadow-[0_0_14px_2px_var(--accent)] md:block"
                  initial={{ left: "1%", opacity: 0 }}
                  animate={{ left: DOT_LEFT, opacity: [0, 1, 1, 1, 1, 0] }}
                  transition={{
                    duration: PIPE_D,
                    times: DOT_TIMES,
                    ease: ["linear", "easeInOut", "linear", "easeInOut", "linear"],
                    repeat: Infinity,
                    repeatDelay: PIPE_RD,
                    delay,
                  }}
                />
              ))
            : null}
          {/* approve 节点：光点停顿时金色 ring 脉冲 + 核验记号（周期 = 半管道周期，对齐两光点） */}
          {!reduce ? (
            <>
              <motion.span
                aria-hidden
                className="absolute left-1/2 top-[7px] hidden size-5 -translate-x-1/2 -translate-y-1/2 rounded-full border border-gold/80 md:block"
                initial={{ opacity: 0, scale: 0.5 }}
                animate={{ opacity: [0.85, 0, 0], scale: [0.5, 1.9, 1.9] }}
                transition={{
                  duration: PERIOD / 2,
                  times: [0, 0.34, 1],
                  ease: "easeOut",
                  repeat: Infinity,
                  delay: APPROVE_AT,
                }}
              />
              <motion.span
                aria-hidden
                className="absolute left-1/2 top-[7px] hidden -translate-x-1/2 -translate-y-[160%] font-mono text-[10px] text-gold md:block"
                initial={{ opacity: 0 }}
                animate={{ opacity: [0, 1, 0, 0] }}
                transition={{
                  duration: PERIOD / 2,
                  times: [0, 0.1, 0.42, 1],
                  repeat: Infinity,
                  delay: APPROVE_AT,
                }}
              >
                ✓
              </motion.span>
            </>
          ) : (
            <span
              aria-hidden
              className="absolute left-1/2 top-[7px] hidden -translate-x-1/2 -translate-y-[160%] font-mono text-[10px] text-gold md:block"
            >
              ✓
            </span>
          )}

          {/* 节点 + 标签：移动端纵排（圆点在左、文字在右），md 起恢复横向等距 */}
          <div className="relative flex flex-col gap-7 md:flex-row md:items-start md:justify-between md:gap-3">
            {FLOW.map((n) => (
              <motion.div
                key={n.key}
                variants={fadeUp}
                className="flex items-start gap-4 md:max-w-[10rem] md:flex-col md:items-center md:gap-0 md:text-center"
              >
                <span
                  className={
                    "mt-0.5 size-3.5 shrink-0 rounded-full ring-4 ring-bg md:mt-0 " +
                    (n.tone === "bull"
                      ? "bg-bull"
                      : n.tone === "muted"
                        ? "bg-fg-muted/60"
                        : "bg-cyan")
                  }
                />
                {/* md:contents —— 桌面端解散包裹层，让标签/描述直接成为纵列 flex 子项 */}
                <div className="md:contents">
                  <span
                    className={
                      "font-mono text-[12px] md:mt-4 " +
                      (n.tone === "bull"
                        ? "text-bull"
                        : n.tone === "muted"
                          ? "text-fg-muted"
                          : "text-cyan")
                    }
                  >
                    {n.tool}
                  </span>
                  {n.desc ? (
                    <span className="mt-1 block text-[12.5px] leading-snug text-fg-muted/80 md:mt-1.5">
                      {n.desc}
                    </span>
                  ) : null}
                </div>
              </motion.div>
            ))}
          </div>
        </div>

        {/* 审计回执：每过一单追加一行（最新在上，渐次变旧淡出） */}
        <motion.div
          variants={fadeUp}
          aria-hidden
          className="mt-10 hidden h-[4.25rem] flex-col gap-1.5 overflow-hidden border-t border-fg/10 pt-3 md:flex"
        >
          {reduce ? (
            <div className="flex items-center gap-3 font-mono text-[11px] text-fg-muted/70">
              <span className="text-fg-muted/45">{receiptTime(0)}</span>
              <span>plan#{receiptId(0)}</span>
              <span className="text-bull/80">approved</span>
              <span className="text-fg-muted/45">· one-shot token</span>
            </div>
          ) : (
            <AnimatePresence initial={false}>
              {receipts.map((r, i) => (
                <motion.div
                  key={r.seq}
                  layout
                  initial={{ opacity: 0, y: -8 }}
                  animate={{ opacity: 1 - i * 0.32, y: 0 }}
                  exit={{ opacity: 0 }}
                  transition={{ duration: 0.35 }}
                  className="flex items-center gap-3 font-mono text-[11px] text-fg-muted/70"
                >
                  <span className="text-fg-muted/45">{receiptTime(r.seq)}</span>
                  <span>plan#{receiptId(r.seq)}</span>
                  <span className="text-bull/80">approved</span>
                  <span className="text-fg-muted/45">· one-shot token</span>
                </motion.div>
              ))}
            </AnimatePresence>
          )}
        </motion.div>

        {/* 框架级持仓保护 —— 审批之外的兜底止损，D2 临床面（gold = risk gate） */}
        <motion.div
          variants={fadeUp}
          className="mt-10 flex flex-col gap-3 border-l-2 border-gold/60 pl-4 md:flex-row md:items-baseline md:gap-5"
        >
          <span className="shrink-0 font-mono text-[10.5px] uppercase tracking-[0.18em] text-gold">
            {t("guard.tag")} · {t("guard.label")}
          </span>
          <p className="max-w-[64ch] text-[13.5px] leading-relaxed text-fg-muted">
            {t("guard.body")}
          </p>
        </motion.div>

        {/* LLM 直连被 deny 拦截：红点加速撞墙、✕ 闪烁抖动、弹回
            移动端两段式（标签一行 + 拦截线一行），md 起恢复 agent—✕—order 单行 */}
        <motion.div
          variants={fadeUp}
          className="mt-12 flex flex-col gap-3 md:flex-row md:items-center md:gap-4"
        >
          <div className="flex items-center justify-between gap-4 md:contents">
            <span className="shrink-0 font-mono text-[11px] uppercase tracking-[0.16em] text-fg-muted">
              {t("agentLabel")}
            </span>
            <span className="shrink-0 font-mono text-[11px] uppercase tracking-[0.16em] text-fg-muted/40 line-through md:order-last">
              {t("orderLabel")}
            </span>
          </div>
          <div className="relative h-5 md:flex-1">
            <div
              className="absolute left-0 right-0 top-1/2 h-px -translate-y-1/2 border-t border-dashed border-fox-red/35"
              aria-hidden
            />
            {/* ✕ 拦截点：撞击瞬间放大闪烁 + 标签微抖 */}
            <motion.span
              className="absolute left-1/2 top-1/2 flex -translate-x-1/2 -translate-y-1/2 items-center gap-1.5 whitespace-nowrap bg-bg px-2 font-mono text-[11px] uppercase tracking-[0.14em] text-fox-red/85"
              animate={reduce ? undefined : { x: [0, 0, -1.5, 1.5, 0, 0] }}
              transition={
                reduce
                  ? undefined
                  : {
                      duration: 3,
                      times: [0, 0.32, 0.36, 0.4, 0.46, 1],
                      repeat: Infinity,
                      repeatDelay: 0.5,
                    }
              }
            >
              <motion.span
                aria-hidden
                animate={reduce ? undefined : { scale: [1, 1, 1.7, 1, 1] }}
                transition={
                  reduce
                    ? undefined
                    : {
                        duration: 3,
                        times: [0, 0.32, 0.38, 0.5, 1],
                        repeat: Infinity,
                        repeatDelay: 0.5,
                      }
                }
              >
                ✕
              </motion.span>
              {t("wall")}
            </motion.span>
            {!reduce ? (
              <motion.span
                aria-hidden
                className="absolute top-1/2 hidden size-2 -translate-y-1/2 rounded-full bg-fox-red md:block"
                initial={{ left: "0%", opacity: 0 }}
                animate={{
                  left: ["0%", "0%", "40%", "33%", "37.5%", "35.5%", "0%"],
                  opacity: [0, 1, 1, 1, 1, 1, 0],
                }}
                transition={{
                  duration: 3,
                  times: [0, 0.07, 0.32, 0.44, 0.55, 0.66, 1],
                  ease: ["linear", "easeIn", "easeOut", "easeInOut", "easeInOut", "easeInOut"],
                  repeat: Infinity,
                  repeatDelay: 0.5,
                }}
              />
            ) : null}
          </div>
        </motion.div>
      </motion.div>
    </section>
  );
}
