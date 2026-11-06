"use client";

import { useTranslations } from "next-intl";
import { evolutionUsd } from "@/lib/evolution-cost";
import type { EvolutionRunSummary } from "@/lib/types";

/** Shows one shared preparation turn, separate from research-stage model charges. */
export function EvolutionChatCosts({
  costs: chat,
}: {
  costs: EvolutionRunSummary["chat_preparation_costs"];
}) {
  const t = useTranslations("evolution.costs");
  const money = (value: number | null) => evolutionUsd(value) ?? t("unavailable");
  return (
    <section className="rounded-lg border border-border-subtle p-3">
      <h3 className="text-sm text-fg">{t("chatTitle")}</h3>
      {chat?.linked ? (
        <>
          <p className="mt-1 text-xs text-fg-muted">
            {t("chatReceipt", {
              calls: chat.call_count ?? t("unavailable"),
              known: money(chat.known_cost_usd ?? null),
              unknown: chat.unknown_cost_count ?? t("unavailable"),
            })}
          </p>
          {(chat.shared_approval_count ?? 0) > 1 && (
            <p className="mt-1 text-xs text-fg-muted">{t("chatShared")}</p>
          )}
        </>
      ) : (
        <p className="mt-1 text-xs text-fg-muted">{t("chatUnlinked")}</p>
      )}
      <p className="mt-1 text-xs text-fg-muted">{t("chatScope")}</p>
    </section>
  );
}
