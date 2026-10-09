"use client";

import { useTranslations } from "next-intl";
import type { EvolutionRun } from "@/lib/types";
import { evolutionCosts, evolutionUsd } from "@/lib/evolution-cost";
import { Panel } from "@/components/ui/Panel";

/** Separate receipts, uncertain usage and repair costs without implying a complete bill. */
export function EvolutionCosts({ run }: { run: EvolutionRun }) {
  const t = useTranslations("evolution.costs");
  const costs = evolutionCosts(run);
  const chat = run.chat_preparation_costs;
  const money = (value: number | null) =>
    evolutionUsd(value) ?? t("unavailable");
  return (
    <Panel title={t("title")}>
      <div className="space-y-4 p-4">
        <dl className="grid gap-4 sm:grid-cols-3">
          {[
            ["known", money(costs.known)],
            ["recorded", money(costs.recorded)],
            ["unknown", String(costs.unknown)],
          ].map(([key, value]) => (
            <div key={key}>
              <dt className="text-xs text-fg-muted">{t(key)}</dt>
              <dd className="mt-1 font-mono text-lg text-fg">{value}</dd>
            </div>
          ))}
        </dl>
        <p className="text-xs text-fg-muted">{t("receiptHint")}</p>
        <div className="grid gap-3 sm:grid-cols-2">
          {costs.phases.map((phase) => (
            <section
              key={phase.key}
              className="rounded-lg border border-border-subtle p-3"
            >
              <h3 className="text-sm text-fg">{t(phase.key)}</h3>
              <p className="mt-1 text-xs text-fg-muted">
                {t("phase", {
                  calls: phase.calls,
                  known: money(phase.known),
                  unknown: phase.unknown,
                })}
              </p>
            </section>
          ))}
        </div>
        <p className="text-xs text-fg-muted">
          {t("failed", {
            known: money(costs.rejectedKnown),
            unknown: costs.rejectedUnknown,
          })}
        </p>
        <section className="rounded-lg border border-border-subtle p-3">
          <h3 className="text-sm text-fg">{t("chatTitle")}</h3>
          {chat?.linked ? (
            <>
              <p className="mt-1 text-xs text-fg-muted">{t("chatReceipt", {
                calls: chat.call_count ?? t("unavailable"),
                known: money(chat.known_cost_usd ?? null),
                unknown: chat.unknown_cost_count ?? t("unavailable"),
              })}</p>
              {(chat.shared_approval_count ?? 0) > 1 && (
                <p className="mt-1 text-xs text-fg-muted">{t("chatShared")}</p>
              )}
            </>
          ) : <p className="mt-1 text-xs text-fg-muted">{t("chatUnlinked")}</p>}
          <p className="mt-1 text-xs text-fg-muted">{t("chatScope")}</p>
        </section>
      </div>
    </Panel>
  );
}
