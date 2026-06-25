"use client";

import * as React from "react";
import { motion, useReducedMotion } from "motion/react";
import { useTranslations } from "next-intl";

import { BroadsheetSection } from "@/components/primitives/BroadsheetSection";

/**
 * 05 — Unified kernel。把「同一份策略代码，三种模式」做成可切换器物：
 * 切 backtest / paper / live 时，策略代码恒定，只有 Clock + Gateway 两行变并高亮。
 * 自动轮转，也可点 tab。代码是 D2 临床面 → 等宽精确。
 */
const MODES = [
  { id: "backtest", clock: "SimClock(bars_2024)", gateway: "SimGateway()" },
  { id: "paper", clock: "LiveClock()", gateway: "PaperGateway()" },
  { id: "live-runner", clock: "LiveClock()", gateway: "CaptureGateway()" },
] as const;

export function UnifiedKernel() {
  const t = useTranslations("kernel");
  const reduce = useReducedMotion();
  const [i, setI] = React.useState(0);
  const [pinned, setPinned] = React.useState(false);

  React.useEffect(() => {
    if (reduce || pinned) return;
    const id = setInterval(() => setI((p) => (p + 1) % MODES.length), 2000);
    return () => clearInterval(id);
  }, [reduce, pinned]);

  const mode = MODES[i];

  return (
    <BroadsheetSection
      index="06"
      align="left"
      indexSide="right"
      eyebrow="Unified kernel · same code, three modes"
      title=""
      titleNode={
        <>
          {t("title")}
          <br />
          <span className="text-gold">{t("titleAlt")}</span>
        </>
      }
      intro={t("sub")}
    >
      <div className="space-y-8">
        {/* 三模式切换器 */}
        <div className="overflow-hidden rounded-md border border-border-subtle bg-bg-elev">
          {/* tabs */}
          <div className="flex border-b border-border-subtle font-mono text-[12px]">
            {MODES.map((m, idx) => {
              const on = idx === i;
              return (
                <button
                  key={m.id}
                  type="button"
                  onClick={() => {
                    setI(idx);
                    setPinned(true);
                  }}
                  className={
                    "relative px-5 py-2.5 uppercase tracking-[0.16em] transition-colors " +
                    (on ? "text-cyan" : "text-fg-muted/60 hover:text-fg")
                  }
                >
                  {m.id}
                  {on ? (
                    <motion.span
                      layoutId="kernel-tab"
                      className="absolute inset-x-0 bottom-0 h-[2px] bg-cyan"
                    />
                  ) : null}
                </button>
              );
            })}
          </div>

          {/* code —— strategy 恒定，clock/gateway 随 mode 变并闪 */}
          <div className="space-y-1 p-5 font-mono text-[13.5px] leading-relaxed">
            <div className="text-fg-muted/50">strategy = MomentumStrategy(params)</div>
            <CodeRow flashKey={`clock-${i}`} label="clock  " value={mode.clock} />
            <CodeRow flashKey={`gw-${i}`} label="gateway" value={mode.gateway} />
            <div className="text-fg-muted/50">engine.run(strategy, clock, gateway)</div>
          </div>

          <div className="border-t border-border-subtle px-5 py-3 font-mono text-[11px] uppercase tracking-[0.16em] text-fg-muted/60">
            only clock + gateway change · your strategy never does
          </div>
        </div>

        {/* Three service plates */}
        <div className="grid gap-px bg-fg/10 md:grid-cols-3">
          {(["data", "paper", "research"] as const).map((id, idx) => (
            <article
              key={id}
              className="group relative flex flex-col gap-3 overflow-hidden bg-bg p-6 transition-colors duration-300 hover:bg-bg-deep"
            >
              <span
                aria-hidden
                className="absolute left-0 top-0 h-full w-0 bg-cyan transition-all duration-300 group-hover:w-[2px]"
              />
              <header className="relative flex items-baseline justify-between">
                <span className="font-mono text-[11px] uppercase tracking-[0.24em] text-fg-muted/70 transition-colors group-hover:text-cyan/80">
                  {String(idx + 1).padStart(2, "0")} / kernel
                </span>
                <span className="font-mono text-[10px] uppercase tracking-[0.2em] text-fg-muted/60">
                  {t(`services.${id}.version`)}
                </span>
              </header>
              <h4
                className="relative display-italic leading-tight text-fg"
                style={{ fontSize: "clamp(1.5rem, 2.4vw, 2rem)", fontWeight: 400 }}
              >
                {t(`services.${id}.title`)}
              </h4>
              <p className="relative text-[14px] leading-relaxed text-fg-muted transition-colors group-hover:text-fg/80">
                {t(`services.${id}.desc`)}
              </p>
              <code className="relative mt-auto border-l-2 border-cyan/40 bg-bg-deep/60 px-3 py-2 font-mono text-[12px] text-cyan/90 transition-all duration-300 group-hover:border-cyan group-hover:text-cyan">
                {t(`services.${id}.code`)}
              </code>
            </article>
          ))}
        </div>
      </div>
    </BroadsheetSection>
  );
}

function CodeRow({
  flashKey,
  label,
  value,
}: {
  flashKey: string;
  label: string;
  value: string;
}) {
  return (
    <div className="flex items-center gap-2">
      <span className="text-fg-muted/50">{label} =</span>
      <motion.span
        key={flashKey}
        initial={{ backgroundColor: "color-mix(in oklab, var(--accent) 22%, transparent)" }}
        animate={{ backgroundColor: "color-mix(in oklab, var(--accent) 0%, transparent)" }}
        transition={{ duration: 1 }}
        className="rounded-sm px-1 text-cyan"
      >
        {value}
      </motion.span>
    </div>
  );
}
