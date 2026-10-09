"use client";

import { useTranslations } from "next-intl";

import type { EvolutionRun } from "@/lib/types";
import { Panel } from "@/components/ui/Panel";

/** 展示 seed、冻结 LLM/数据快照、基准与失败信息。 */
export function EvolutionRunData({ run }: { run: EvolutionRun }) {
  const t = useTranslations("evolution.detail");
  const w = useTranslations("evolution.workflow");
  const labels = Object.fromEntries(
    [
      "fitness",
      "sharpe",
      "total_return_pct",
      "max_drawdown_pct",
      "num_trades",
      "calmar",
      "sortino",
      "win_rate",
    ].map((key) => [key, w(`metrics.${key}`)]),
  );
  return (
    <div className="grid grid-cols-1 gap-6 xl:grid-cols-2">
      <Panel title={t("summary")}>
        <dl className="grid grid-cols-2 gap-4 p-4 text-sm">
          <Item
            label={t("stage")}
            value={
              run.active_stage && w.has(`stages.${run.active_stage}`)
                ? w(`stages.${run.active_stage}`)
                : w("stages.unknown")
            }
          />
          <Item
            label={t("attempts")}
            value={`${run.attempted}/${run.budget}`}
          />
          <Item label={t("succeeded")} value={String(run.succeeded)} />
          <Item label={t("rejected")} value={String(run.rejected)} />
          <Item label={t("cost")} value={`$${run.llm_cost_usd.toFixed(4)}`} />
        </dl>
        {run.failure_message && (
          <p className="border-t border-border-subtle px-4 py-3 text-sm text-fox-red">
            {run.failure_message}
          </p>
        )}
      </Panel>
      <details className="rounded-xl border border-border-subtle xl:col-span-2">
        <summary className="cursor-pointer p-4 text-sm text-fg-muted">
          {w("audit")}
        </summary>
        <div className="grid gap-4 p-4 sm:grid-cols-2">
          <p className="text-sm text-fg-muted sm:col-span-2">
            {w("auditHint")}
          </p>
          <p className="break-all font-mono text-xs text-fg-muted sm:col-span-2">
            {w("recordId")}: {run.run_id} · {run.seed_strategy_id}
          </p>
          <Panel title={t("llmSnapshot")}>
            <ObjectRows
              value={llmSnapshotRows(run)}
              empty={t("notAvailable")}
              preferred={[
                "provider",
                "model",
                "config_id",
                "pricing_version",
                "estimated_max_usd_per_candidate",
                "config_digest",
              ]}
            />
          </Panel>
          <Panel title={t("dataset")}>
            <ObjectRows
              value={run.dataset_manifest}
              empty={t("notAvailable")}
              preferred={[
                "venue",
                "symbol",
                "requested_timeframe",
                "latest_bar_ts",
                "bar_count",
                "freshness_lag_seconds",
                "content_sha256",
              ]}
            />
          </Panel>
          <Panel title={t("seedReport")}>
            <ObjectRows
              value={reportMetrics(run.seed_report_snapshot)}
              empty={t("notAvailable")}
              preferred={Object.keys(labels)}
              labels={labels}
            />
          </Panel>
          <Panel title={t("baseline")}>
            <ObjectRows
              value={reportMetrics(run.baseline_snapshot)}
              empty={t("notAvailable")}
              preferred={Object.keys(labels)}
              labels={labels}
            />
          </Panel>
        </div>
      </details>
    </div>
  );
}

function llmSnapshotRows(run: EvolutionRun): Record<string, unknown> | null {
  const snapshot = run.llm_snapshot;
  if (!snapshot) return null;
  return {
    provider: snapshot.provider,
    model: snapshot.model,
    config_id: snapshot.config_id,
    pricing_version: snapshot.pricing.version,
    estimated_max_usd_per_candidate:
      snapshot.pricing.estimated_max_usd_per_candidate,
    config_digest: run.llm_config_digest ?? snapshot.config_digest,
  };
}

function Item({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0">
      <dt className="font-mono text-[10px] uppercase tracking-wider text-fg-muted">
        {label}
      </dt>
      <dd className="mt-1 break-all font-mono text-xs text-fg">{value}</dd>
    </div>
  );
}

function ObjectRows({
  value,
  empty,
  preferred,
  labels,
}: {
  value: Record<string, unknown> | null;
  empty: string;
  preferred?: string[];
  labels?: Record<string, string>;
}) {
  if (!value) return <p className="p-4 text-sm text-fg-muted">{empty}</p>;
  const entries = preferred
    ? preferred
        .filter((key) => key in value)
        .map((key) => [key, value[key]] as const)
    : Object.entries(value)
        .filter(([, item]) => typeof item !== "object")
        .slice(0, 12);
  if (!entries.length)
    return <p className="p-4 text-sm text-fg-muted">{empty}</p>;
  return (
    <dl className="divide-y divide-border-subtle/60 px-4">
      {entries.map(([key, item]) => (
        <div
          key={key}
          className="grid grid-cols-[minmax(0,1fr)_minmax(0,1.6fr)] gap-3 py-2 text-xs"
        >
          <dt className="font-mono text-fg-muted">{labels?.[key] ?? key}</dt>
          <dd className="break-all text-right font-mono text-fg">
            {formatValue(item)}
          </dd>
        </div>
      ))}
    </dl>
  );
}

function formatValue(value: unknown): string {
  if (typeof value === "number")
    return Number.isInteger(value) ? String(value) : value.toFixed(4);
  if (typeof value === "boolean") return value ? "true" : "false";
  return value == null ? "—" : String(value);
}

/** Legacy reports keep discovery metrics under validation.train; never expose holdout metrics here. */
function reportMetrics(
  snapshot: Record<string, unknown> | null,
): Record<string, unknown> | null {
  const validation = snapshot?.validation;
  if (validation && typeof validation === "object" && "train" in validation) {
    const train = validation.train;
    if (train && typeof train === "object" && !Array.isArray(train))
      return train as Record<string, unknown>;
  }
  return snapshot;
}
