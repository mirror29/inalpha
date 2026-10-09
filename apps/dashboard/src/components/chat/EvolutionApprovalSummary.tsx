"use client";

import { useTranslations } from "next-intl";
import { evolutionApprovalSummary } from "@/lib/evolution-approval-summary";
import { evolutionUsd } from "@/lib/evolution-cost";

/** Explain the prepared E1 task before its trusted approval controls. */
export function EvolutionApprovalSummary({ envelope }: { envelope: unknown }) {
  const t = useTranslations("activity.approval.preparation");
  const summary = evolutionApprovalSummary(envelope);
  if (!summary) return null;
  const unavailable = t("unavailable");
  const seed = summary.label ?? (summary.kind === "evolution" && summary.slot !== null
    ? t("evolutionSlot", { slot: summary.slot + 1 }) : t(`seed.${summary.kind}`));
  const rows = [
    [t("seedLabel"), seed],
    [t("market"), `${summary.market || unavailable} · ${summary.mode ? t(summary.mode) : unavailable} · ${summary.timeframe ?? unavailable}`],
    [t("simulation"), summary.initialCash !== null && summary.feeRate !== null && summary.leverage !== null
      ? t("simulationValue", { cash: summary.initialCash, fee: Number((summary.feeRate * 100).toFixed(4)), leverage: summary.leverage }) : unavailable],
    [t("validation"), summary.validationSplit === null ? unavailable : t("validationValue", { percent: Number((summary.validationSplit * 100).toFixed(2)) })],
    [t("window"), summary.from && summary.to ? `${summary.from} → ${summary.to}` : unavailable],
    [t("data"), summary.bars === null ? unavailable : t("bars", { count: summary.bars })],
    [t("latestBar"), summary.latestBar ?? unavailable],
    [t("budget"), summary.budget === null ? unavailable : t("slots", { count: summary.budget })],
    [t("model"), summary.model || unavailable],
    [t("estimatedCost"), evolutionUsd(summary.estimatedCost) ?? unavailable],
  ];
  return <section className="border-b border-border-subtle px-3 py-3">
    <h4 className="mb-3 text-sm font-medium text-fg">{t("title")}</h4>
    <dl className="grid gap-x-4 gap-y-2 text-xs sm:grid-cols-[auto_1fr]">
      {rows.map(([label, value]) => <div key={label} className="contents">
        <dt className="text-fg-muted">{label}</dt>
        <dd className="break-words text-fg">{value}</dd>
      </div>)}
    </dl>
    <p className="mt-3 text-xs text-fg-muted">{t("scope")}</p>
    {summary.warningCount > 0 && <p className="mt-2 text-xs text-gold">{t("warnings", { count: summary.warningCount })}</p>}
    <details className="mt-3 text-xs text-fg-muted">
      <summary className="cursor-pointer">{t("audit")}</summary>
      <dl className="mt-2 space-y-2">
        {[[t("reference"), summary.reference], [t("sourceHash"), summary.seedHash], [t("dataHash"), summary.datasetHash], [t("requestHash"), summary.requestHash]].map(([label, value]) => <div key={label}>
          <dt>{label}</dt><dd className="break-all font-mono">{value ?? unavailable}</dd>
        </div>)}
      </dl>
    </details>
  </section>;
}
