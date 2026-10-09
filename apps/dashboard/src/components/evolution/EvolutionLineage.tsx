"use client";

import { useTranslations } from "next-intl";
import { Link } from "@/i18n/navigation";
import { Panel } from "@/components/ui/Panel";
import { workflowState } from "@/lib/evolution-presentation";
import type { EvolutionLineage as Lineage } from "@/lib/types";

/** Read-only provenance links; completed research never implies trading adoption. */
export function EvolutionLineage({
  lineage,
}: {
  lineage: Lineage | null | undefined;
}) {
  const t = useTranslations("evolution.lineage");
  const w = useTranslations("evolution.workflow");
  const l = useTranslations("evolution.loop");
  const state = (status: string) =>
    w.has(`states.${status}`)
      ? w(`states.${status}`)
      : l.has(`states.${workflowState(status)}`)
        ? l(`states.${workflowState(status)}`)
        : t("statusUnknown");
  return (
    <Panel title={t("title")}>
      {!lineage ? (
        <p className="p-4 text-sm text-fg-muted">{t("unavailable")}</p>
      ) : (
        <div className="space-y-4 p-4">
          <div className="text-sm">
            <p className="text-xs text-fg-muted">{t("origin")}</p>
            {lineage.display_origin?.description && (
              <p className="mt-1 leading-6 text-fg">
                {lineage.display_origin.description}
              </p>
            )}
            {lineage.display_origin?.href ? (
              <Link
                href={lineage.display_origin.href}
                className="text-cyan hover:underline"
              >
                {t("viewOrigin")} →
              </Link>
            ) : (
              <p className="mt-1 text-fg-muted">
                {lineage.source_reference === "sma_cross_v1"
                  ? t("builtinSeed")
                  : t("originUnavailable")}
              </p>
            )}
          </div>
          {lineage.experiment_id && (
            <Link
              href={`/evolution/${lineage.experiment_id}`}
              className="inline-block text-sm text-cyan hover:underline"
            >
              {t("experimentStart")} →
            </Link>
          )}
          <ol
            className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3"
            aria-label={t("attempts")}
          >
            {(lineage.attempts ?? []).map((attempt) => (
              <li
                key={attempt.run_id}
                aria-current={
                  attempt.run_id === lineage.current_run_id ? "step" : undefined
                }
                className="rounded-lg border border-border-subtle p-3 text-sm"
              >
                <Link
                  href={`/evolution/${attempt.run_id}`}
                  className="text-cyan hover:underline"
                >
                  {t("attempt", { number: attempt.attempt_number })} →
                </Link>
                <p className="mt-1 text-xs text-fg-muted">
                  {state(attempt.status)}
                </p>
              </li>
            ))}
          </ol>
          {(lineage.loops ?? []).map((loop, index) => (
            <Link
              key={loop.loop_id}
              href={`/evolution/loops/${loop.loop_id}`}
              className="block text-sm text-cyan hover:underline"
            >
              {t("workflow", { number: index + 1 })} · {state(loop.status)} →
            </Link>
          ))}
          {(lineage.campaigns ?? []).map((campaign, index) => (
            <section
              key={campaign.campaign_id}
              className="rounded-lg border border-border-subtle p-3 text-sm"
            >
              <Link
                href={`/evolution/campaigns/${campaign.campaign_id}`}
                className="text-cyan hover:underline"
              >
                {t("search", { number: index + 1 })} · {state(campaign.status)}{" "}
                →
              </Link>
              <dl className="mt-3 grid gap-3 sm:grid-cols-2">
                <div>
                  <dt className="text-xs text-fg-muted">{t("champion")}</dt>
                  <dd>
                    {campaign.locked_candidate_id
                      ? t("locked")
                      : t("notLocked")}
                  </dd>
                </div>
                <div>
                  <dt className="text-xs text-fg-muted">Forward</dt>
                  <dd>
                    {campaign.forward_sandbox_id
                      ? `${campaign.forward_status && t.has(`forwardStates.${campaign.forward_status}`) ? t(`forwardStates.${campaign.forward_status}`) : t("forwardRecorded")} · ${t("events", { count: campaign.forward_event_count ?? 0 })}`
                      : t("notStarted")}
                  </dd>
                </div>
                <div>
                  <dt className="text-xs text-fg-muted">{t("holdout")}</dt>
                  <dd>
                    {!campaign.holdout_attempt_id
                      ? t("sealed")
                      : campaign.holdout_passed === true
                        ? t("passed")
                        : campaign.holdout_passed === false
                          ? t("notPassed")
                          : t("pendingResult")}
                  </dd>
                </div>
                <div>
                  <dt className="text-xs text-fg-muted">{t("adoption")}</dt>
                  <dd>
                    {t("adoptionRecords", { count: campaign.adoption_count })}
                  </dd>
                </div>
              </dl>
            </section>
          ))}
          {(lineage.campaigns ?? []).length === 0 && (
            <p className="text-xs text-fg-muted">{t("noSearch")}</p>
          )}
          <p className="text-xs leading-6 text-fg-muted">{t("boundary")}</p>
        </div>
      )}
    </Panel>
  );
}
