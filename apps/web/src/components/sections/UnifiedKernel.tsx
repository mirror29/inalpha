"use client";

import { useTranslations } from "next-intl";

import { BroadsheetSection } from "@/components/primitives/BroadsheetSection";
import { CodeDiff } from "@/components/primitives/CodeDiff";

/**
 * 04 — Unified kernel. The "backtest = paper = live" promise made concrete
 * via a one-line CodeDiff: swap engines, not behavior.
 */
export function UnifiedKernel() {
  const t = useTranslations("kernel");

  return (
    <BroadsheetSection
      index="04"
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
        <CodeDiff
          beforeLabel={t("diff.beforeLabel")}
          afterLabel={t("diff.afterLabel")}
          before={[
            "from inalpha_paper import BacktestEngine",
            "",
            "engine = BacktestEngine(bars=bars_2024)",
            "strategy.run(engine)",
          ]}
          after={[
            "from inalpha_paper import LiveEngine",
            "",
            "engine = LiveEngine(broker=ibkr)",
            "strategy.run(engine)",
          ]}
        />

        {/* Three service plates */}
        <div className="grid gap-px bg-fg/10 md:grid-cols-3">
          {(["data", "paper", "research"] as const).map((id, i) => (
            <article
              key={id}
              className="group relative flex flex-col gap-3 overflow-hidden bg-bg p-6 transition-colors duration-300 hover:bg-bg-deep"
            >
              {/* 左 accent 竖线：rest 0px slide in 到 2px */}
              <span
                aria-hidden
                className="absolute left-0 top-0 h-full w-0 bg-cyan transition-all duration-300 group-hover:w-[2px]"
              />
              {/* cyan radial glow，hover 淡入 */}
              <span
                aria-hidden
                className="pointer-events-none absolute inset-0 opacity-0 transition-opacity duration-300 group-hover:opacity-100"
                style={{
                  background:
                    "radial-gradient(ellipse at 0% 100%, rgba(95,179,255,0.10), transparent 60%)",
                }}
              />
              <header className="relative flex items-baseline justify-between">
                <span className="font-mono text-[11px] uppercase tracking-[0.24em] text-fg-muted/70 transition-colors group-hover:text-cyan/80">
                  {String(i + 1).padStart(2, "0")} / kernel
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
              <code className="relative mt-auto border-l-2 border-cyan/40 bg-bg-deep/60 px-3 py-2 font-mono text-[12px] text-cyan/90 transition-all duration-300 group-hover:border-cyan group-hover:text-cyan group-hover:shadow-[0_0_18px_-6px_rgba(95,179,255,0.45)]">
                {t(`services.${id}.code`)}
              </code>
            </article>
          ))}
        </div>
      </div>
    </BroadsheetSection>
  );
}
