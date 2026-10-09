"use client";

import { useTranslations } from "next-intl";
import { Link } from "@/i18n/navigation";
import {
  evolutionComparison,
  evaluationNumber,
} from "@/lib/evolution-comparison";
import type { EvolutionRun } from "@/lib/types";
import { Panel } from "@/components/ui/Panel";

const METRICS = [
  "fitness",
  "total_return_pct",
  "max_drawdown_pct",
  "sharpe",
  "num_trades",
  "total_fees",
];

/** Human-readable evidence summary without equating a completed experiment with adoption. */
export function EvolutionComparison({ run }: { run: EvolutionRun }) {
  const t = useTranslations("evolution.comparison");
  const comparison = evolutionComparison(run);
  const columns = [
    { key: "seed", report: comparison.seed, comparable: true },
    {
      key: "best",
      report: comparison.bestReport,
      comparable: comparison.candidateComparable,
    },
    {
      key: "benchmark",
      report: comparison.benchmark,
      comparable: comparison.benchmarkComparable,
    },
  ];
  const validation = comparison.bestReport?.validation;
  const validationReport =
    validation && typeof validation === "object" && !Array.isArray(validation)
      ? (validation as Record<string, unknown>)
      : null;
  return (
    <Panel title={t("title")}>
      <div className="space-y-4 p-4">
        <div>
          <p className="text-lg text-fg">
            {t(`verdict.${comparison.verdict}`)}
          </p>
          <p className="mt-1 text-sm text-fg-muted">{t("evidenceBoundary")}</p>
          {comparison.best && (
            <Link
              href={`/evolution/candidates/${comparison.best.candidate_id}`}
              className="mt-2 inline-block text-sm text-cyan hover:underline"
            >
              {t("bestLink", { slot: comparison.best.slot + 1 })}
            </Link>
          )}
        </div>
        <div className="grid gap-3 md:grid-cols-3">
          {columns.map(({ key, report, comparable }) => (
            <section
              key={key}
              className="min-w-0 rounded-lg border border-border-subtle p-3"
            >
              <h3 className="text-sm text-fg">{t(key)}</h3>
              {!report ? (
                <p className="mt-2 text-xs text-fg-muted">{t("unavailable")}</p>
              ) : (
                <>
                  {!comparable && (
                    <p className="mt-2 text-xs text-fox-red">
                      {t("scopeMismatch")}
                    </p>
                  )}
                  <dl className="mt-2 space-y-2">
                    {METRICS.map((metric) => {
                      const value = evaluationNumber(report, metric);
                      return (
                        <div
                          key={metric}
                          className="flex justify-between gap-2 text-xs"
                        >
                          <dt className="text-fg-muted">
                            {t(`metrics.${metric}`)}
                          </dt>
                          <dd className="font-mono text-fg">
                            {value === null
                              ? "—"
                              : metric === "num_trades"
                                ? value
                                : `${value.toFixed(metric === "fitness" || metric === "sharpe" ? 4 : 2)}${metric.endsWith("_pct") ? "%" : ""}`}
                          </dd>
                        </div>
                      );
                    })}
                  </dl>
                </>
              )}
            </section>
          ))}
        </div>
        <p className="text-xs text-fg-muted">
          {t("scope", {
            start: String(comparison.seed?.period_start ?? "—"),
            end: String(comparison.seed?.period_end ?? "—"),
            bars: String(comparison.seed?.num_bars ?? "—"),
            fee:
              typeof run.config.fee_rate === "number"
                ? `${(run.config.fee_rate * 100).toFixed(3)}%`
                : "—",
          })}
        </p>
        {comparison.best && (
          <section className="space-y-2 border-t border-border-subtle pt-3">
            <h3 className="text-sm text-fg">{t("validationTitle")}</h3>
            {validationReport ? (
              <div className="grid gap-3 sm:grid-cols-2">
                {["train", "holdout"].map((segment) => {
                  const value = validationReport[segment];
                  const segmentReport =
                    value && typeof value === "object" && !Array.isArray(value)
                      ? (value as Record<string, unknown>)
                      : null;
                  return (
                    <dl
                      key={segment}
                      className="rounded-lg bg-bg-elev/30 p-3 text-xs"
                    >
                      <dt className="text-fg-muted">{t(segment)}</dt>
                      <dd className="mt-1 text-fg">
                        {t("segmentSummary", {
                          bars: String(
                            evaluationNumber(segmentReport, "num_bars") ?? "—",
                          ),
                          trades: String(
                            evaluationNumber(segmentReport, "num_trades") ??
                              "—",
                          ),
                          result:
                            evaluationNumber(
                              segmentReport,
                              "total_return_pct",
                            )?.toFixed(2) ?? "—",
                        })}
                      </dd>
                    </dl>
                  );
                })}
              </div>
            ) : (
              <p className="text-xs text-fg-muted">
                {t("validationUnavailable")}
              </p>
            )}
            <p className="text-xs text-fg-muted">
              {t("risk", {
                value: t(
                  `risks.${["high", "medium", "low"].includes(comparison.best.overfitting_risk) ? comparison.best.overfitting_risk : "unknown"}`,
                ),
              })}
            </p>
          </section>
        )}
        <p className="text-xs text-fg-muted">{t("selectionHint")}</p>
      </div>
    </Panel>
  );
}
