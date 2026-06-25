"use client";

import { useTranslations } from "next-intl";

import { BroadsheetSection } from "@/components/primitives/BroadsheetSection";

/**
 * 05 — Overfitting gauntlet。把「防过拟合四层防线」摊成临床面网格：
 * 每层一张 plate，列出具体机制（mono chip）+ 一句「挡住什么」。
 * 核心论点：多重检验偏差是真敌人，每个环节各自校正。数字/术语 D2 临床调。
 */
interface Layer {
  tag: string;
  kills: string;
  items: string[];
}

export function OverfittingGuard() {
  const t = useTranslations("robustness");
  const layers = t.raw("layers") as Layer[];

  return (
    <BroadsheetSection
      index="05"
      align="right"
      indexSide="left"
      eyebrow="Robustness · the overfitting gauntlet"
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
      <div className="w-full space-y-8">
        {/* 核心敌人标语 —— 单行临床面 */}
        <div className="flex items-center gap-2.5 border-l-2 border-seal/60 bg-bg-elev px-4 py-3 font-mono text-[11px] uppercase tracking-[0.18em] text-fg-muted">
          <span className="text-fg-muted/55">{t("coreLabel")}</span>
          <span aria-hidden className="text-fg-muted/30">
            ·
          </span>
          <span className="text-seal/90">{t("core")}</span>
        </div>

        {/* 四层防线 plate 网格 */}
        <div className="grid gap-px bg-fg/10 md:grid-cols-2 xl:grid-cols-4">
          {layers.map((layer, idx) => (
            <article
              key={layer.tag}
              className="group relative flex flex-col gap-4 overflow-hidden bg-bg p-6 transition-colors duration-300 hover:bg-bg-deep"
            >
              <span
                aria-hidden
                className="absolute left-0 top-0 h-full w-0 bg-cyan transition-all duration-300 group-hover:w-[2px]"
              />
              <header className="relative flex items-baseline justify-between">
                <span className="font-mono text-[11px] uppercase tracking-[0.24em] text-fg-muted/70 transition-colors group-hover:text-cyan/80">
                  {String(idx + 1).padStart(2, "0")} / {layer.tag}
                </span>
              </header>

              <ul className="relative flex flex-col gap-1.5">
                {layer.items.map((item) => (
                  <li
                    key={item}
                    className="flex items-start gap-2 font-mono text-[12px] leading-snug text-fg-muted transition-colors group-hover:text-fg/80"
                  >
                    <span aria-hidden className="mt-[6px] h-[3px] w-[3px] shrink-0 bg-cyan/50" />
                    <span>{item}</span>
                  </li>
                ))}
              </ul>

              <p className="relative mt-auto border-t border-border-subtle pt-3 text-[13px] leading-relaxed text-fg/85">
                <span className="font-mono text-[10px] uppercase tracking-[0.2em] text-seal/80">
                  {t("killLabel")}
                </span>{" "}
                {layer.kills}
              </p>
            </article>
          ))}
        </div>
      </div>
    </BroadsheetSection>
  );
}
