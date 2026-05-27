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
              className="group relative flex flex-col gap-3 bg-bg p-6 transition-colors hover:bg-bg-deep"
            >
              <header className="flex items-baseline justify-between">
                <span className="font-mono text-[11px] uppercase tracking-[0.24em] text-fg-muted/70">
                  {String(i + 1).padStart(2, "0")} / kernel
                </span>
                <span className="font-mono text-[10px] uppercase tracking-[0.2em] text-fg-muted/60">
                  {t(`services.${id}.version`)}
                </span>
              </header>
              <h4
                className="display-italic leading-tight text-fg"
                style={{ fontSize: "clamp(1.5rem, 2.4vw, 2rem)", fontWeight: 400 }}
              >
                {t(`services.${id}.title`)}
              </h4>
              <p className="text-[14px] leading-relaxed text-fg-muted">
                {t(`services.${id}.desc`)}
              </p>
              <code className="mt-auto border-l-2 border-cyan/40 bg-bg-deep/60 px-3 py-2 font-mono text-[12px] text-cyan/90">
                {t(`services.${id}.code`)}
              </code>
            </article>
          ))}
        </div>
      </div>
    </BroadsheetSection>
  );
}
