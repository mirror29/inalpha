"use client";

import { useLocale, useTranslations } from "next-intl";
import { Link } from "@/i18n/navigation";
import { fmtRelative } from "@/lib/format";
import { evolutionMarket, strategyTitle } from "@/lib/evolution-presentation";
import { loopStage } from "@/lib/evolution-loop";
import type { EvolutionLoop } from "@/lib/types";
import { Panel } from "@/components/ui/Panel";
import { TableEmpty } from "@/components/ui/Table";
import { StatusBadge } from "@/components/ui/StatusBadge";

/** Present each complete research task through its strategy, result, and next step. */
export function EvolutionLoopTable({ loops }: { loops: EvolutionLoop[] }) {
  const t = useTranslations("evolution");
  const locale = useLocale();
  return (
    <Panel
      className="@container"
      title={t("workflow.tasks")}
      aside={
        <span className="text-xs text-fg-muted">
          {t("workflow.loaded", { count: loops.length })}
        </span>
      }
    >
      {loops.length === 0 ? (
        <div className="p-6">
          <TableEmpty>{t("loop.empty")}</TableEmpty>
          <Link
            href="/lab"
            className="mt-3 inline-block text-sm text-cyan hover:underline"
          >
            {t("workflow.chooseStrategy")} →
          </Link>
        </div>
      ) : (
        <div className="divide-y divide-border-subtle">
          {loops.map((loop) => (
            <article
              key={loop.loop_id}
              className="grid gap-5 p-5 @min-[48rem]:grid-cols-[minmax(0,1.2fr)_minmax(0,1fr)_auto]"
            >
              <div className="min-w-0">
                <p className="mb-2 text-xs text-fg-muted">
                  {evolutionMarket(loop.frozen_config) ||
                    t("workflow.marketUnavailable")}
                </p>
                <Link
                  href={`/evolution/loops/${loop.loop_id}`}
                  title={loop.display_target?.description}
                  className="line-clamp-2 block text-base font-medium leading-6 text-cyan hover:underline [overflow-wrap:anywhere]"
                >
                  {strategyTitle(loop.display_target?.description) ||
                    t(`workflow.targets.${loop.target_kind}`)}
                </Link>
                <p className="mt-2 text-xs text-fg-muted">
                  {fmtRelative(loop.updated_at, Date.now(), locale)}
                </p>
              </div>
              <div>
                <StatusBadge
                  label={t(`loop.states.${loop.status}`)}
                  tone={
                    loop.status === "adoption_ready"
                      ? "bull"
                      : loop.status === "failed"
                        ? "fox"
                        : "muted"
                  }
                  dot
                />
                <p className="mt-3 text-sm leading-6 text-fg-muted">
                  {t(`loop.guidance.${loop.status}`)}
                </p>
              </div>
              <div className="flex items-center justify-between gap-4 @min-[48rem]:flex-col @min-[48rem]:items-end @min-[48rem]:justify-center">
                <div className="text-sm">
                  <span className="text-fg-muted">
                    {t("loop.cost.spent_usd")}{" "}
                  </span>
                  <span className="font-mono">
                    {loop.budget_usage
                      ? `$${loop.budget_usage.spent_usd.toFixed(4)}`
                      : t("workflow.unknown")}
                  </span>
                </div>
                <Link
                  href={`/evolution/loops/${loop.loop_id}`}
                  className="text-sm text-cyan hover:underline"
                >
                  {t(
                    loopStage(loop.status) === 4
                      ? "workflow.reviewAdoption"
                      : "workflow.viewResearch",
                  )}{" "}
                  →
                </Link>
              </div>
            </article>
          ))}
        </div>
      )}
    </Panel>
  );
}
