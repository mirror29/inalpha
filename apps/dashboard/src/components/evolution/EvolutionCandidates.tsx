"use client";

import { useTranslations } from "next-intl";

import { Link } from "@/i18n/navigation";
import type { EvolutionCandidateSummary } from "@/lib/types";
import { candidateCost, evolutionUsd } from "@/lib/evolution-cost";
import { StatusBadge } from "@/components/ui/StatusBadge";

/** 按 slot 展示一代候选及其阶段结果。 */
export function EvolutionCandidates({
  candidates,
}: {
  candidates: EvolutionCandidateSummary[];
}) {
  const t = useTranslations("evolution.detail");
  const w = useTranslations("evolution.workflow");
  const c = useTranslations("evolution.costs");
  if (candidates.length === 0) {
    return (
      <p className="p-8 text-center text-sm text-fg-muted">
        {t("noCandidates")}
      </p>
    );
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead className="border-b border-border-subtle font-mono text-[10px] uppercase tracking-wider text-fg-muted">
          <tr>
            <th className="px-4 py-2 text-left">{t("slot")}</th>
            <th className="px-4 py-2 text-left">{t("outcome")}</th>
            <th className="px-4 py-2 text-left">{t("stage")}</th>
            <th className="px-4 py-2 text-right">{t("fitness")}</th>
            <th className="px-4 py-2 text-right">{t("cost")}</th>
          </tr>
        </thead>
        <tbody>
          {candidates.map((candidate) => {
            const cost = candidateCost(candidate);
            const parent = candidates.find(
              (item) => item.candidate_id === candidate.parent_id,
            );
            return (
              <tr
                key={candidate.candidate_id}
                className="border-b border-border-subtle/60 last:border-0"
              >
                <td className="px-4 py-3 font-mono">
                  <Link
                    href={`/evolution/candidates/${candidate.candidate_id}`}
                    className="text-cyan hover:underline"
                  >
                    #{candidate.slot + 1}
                  </Link>
                  {candidate.parent_id && (
                    <Link
                      href={`/evolution/candidates/${candidate.parent_id}`}
                      className="mt-1 block text-xs text-fg-muted hover:text-cyan"
                    >
                      {parent
                        ? c("repairParent", { slot: parent.slot + 1 })
                        : c("repairParentLink")}
                    </Link>
                  )}
                </td>
                <td className="px-4 py-3">
                  <StatusBadge
                    label={
                      w.has(`outcomes.${candidate.outcome}`)
                        ? w(`outcomes.${candidate.outcome}`)
                        : candidate.outcome
                    }
                    tone={outcomeTone(candidate.outcome)}
                  />
                </td>
                <td className="px-4 py-3 font-mono text-xs text-fg-muted">
                  {w.has(`stages.${candidate.stage}`)
                    ? w(`stages.${candidate.stage}`)
                    : w("stages.unknown")}
                </td>
                <td className="px-4 py-3 text-right font-mono">
                  {candidate.fitness?.toFixed(4) ?? "—"}
                </td>
                <td className="px-4 py-3 text-right font-mono text-fg-muted">
                  {cost.state === "known"
                    ? evolutionUsd(cost.confirmed)
                    : c(
                        cost.state === "notCalled"
                          ? "notCalled"
                          : "unknownState",
                      )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function outcomeTone(outcome: string): "bull" | "fox" | "gold" | "muted" {
  if (outcome === "succeeded") return "bull";
  if (outcome === "pending") return "gold";
  if (outcome === "cancelled") return "muted";
  return "fox";
}
