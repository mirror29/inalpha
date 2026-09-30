"use client";

import { useTranslations } from "next-intl";

import { evolutionMarket } from "@/lib/evolution-presentation";
import type { EvolutionRun } from "@/lib/types";
import { evolutionTone, isEvolutionActive } from "@/lib/evolution";
import { LiveStrip } from "@/components/ui/LiveStrip";
import { StatusBadge } from "@/components/ui/StatusBadge";

/** 演化详情页头及 active run 操作。 */
export function EvolutionRunHeader({
  run,
  asOf,
  isValidating,
  isStaleFrame,
  onAbort,
}: {
  run: EvolutionRun;
  asOf: string;
  isValidating: boolean;
  isStaleFrame: boolean;
  onAbort: () => void;
}) {
  const t = useTranslations("evolution.detail");
  const w = useTranslations("evolution.workflow");
  const active = isEvolutionActive(run.status);
  return (
    <header className="flex flex-col gap-4 border-b border-border-subtle pb-5 lg:flex-row lg:items-end lg:justify-between">
      <div>
        <div className="flex flex-wrap items-center gap-3">
          <h1 className="font-display text-3xl text-fg lg:text-4xl">
            {t("title")}
          </h1>
          <StatusBadge
            label={w(`states.${run.status}`)}
            tone={evolutionTone(run.status)}
            dot
            pulse={active}
          />
        </div>
        <p className="mt-2 font-mono text-xs text-fg-muted">
          {evolutionMarket(run.config) || w("marketUnavailable")}
        </p>
      </div>
      <div className="flex flex-wrap items-center gap-3">
        {active && (
          <button
            type="button"
            onClick={onAbort}
            className="rounded-md border border-fox-red/40 px-3 py-1.5 font-mono text-xs text-fox-red hover:bg-fox-red/10"
          >
            {t("abort")}
          </button>
        )}
        <LiveStrip
          asOf={asOf}
          isValidating={isValidating}
          isStaleFrame={isStaleFrame}
        />
      </div>
    </header>
  );
}
