"use client";

import { useTranslations } from "next-intl";
import { TriangleAlert } from "lucide-react";

/**
 * FX 折算告警 —— account.fx_warnings 非空时显示。
 * 金融时效硬约束:权益可能不完整,必须显式告知,不静默。
 */
export function FxWarningBanner({ warnings }: { warnings: string[] }) {
  const t = useTranslations("overview.fxWarning");
  if (warnings.length === 0) return null;

  return (
    <div className="rounded-lg border border-gold/30 bg-gold/[0.07] px-4 py-3">
      <div className="flex items-start gap-2.5">
        <TriangleAlert
          className="mt-0.5 size-4 shrink-0 text-gold"
          strokeWidth={2}
        />
        <div className="min-w-0">
          <div className="font-mono text-xs font-medium uppercase tracking-wider text-gold">
            {t("title")}
          </div>
          <p className="mt-1 text-sm text-fg-muted">{t("body")}</p>
          <ul className="mt-2 flex flex-wrap gap-1.5">
            {warnings.map((w, i) => (
              <li
                key={i}
                className="rounded border border-gold/20 bg-bg-deep/40 px-2 py-0.5 font-mono text-[11px] text-gold/90"
              >
                {w}
              </li>
            ))}
          </ul>
        </div>
      </div>
    </div>
  );
}
