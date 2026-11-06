"use client";

import { useTranslations } from "next-intl";
import { hypothesisParents } from "@/lib/evolution-hypothesis-lineage";
import type { EvolutionHypothesis } from "@/lib/types";

/** Navigates recorded hypothesis ancestry without displaying hashes as names. */
export function EvolutionHypothesisLineage({
  hypothesis,
  hypotheses,
  onInspect,
}: {
  hypothesis: EvolutionHypothesis;
  hypotheses: EvolutionHypothesis[];
  onInspect: (parent: EvolutionHypothesis) => void;
}) {
  const t = useTranslations("evolution.lineage");
  const parents = hypothesisParents(hypothesis, hypotheses);
  return (
    <div className="mt-3 space-y-1 border-t border-border-subtle pt-2 text-xs text-fg-muted">
      <p>
        {t.has(`mutationKinds.${hypothesis.lineage_kind}`)
          ? t(`mutationKinds.${hypothesis.lineage_kind}`)
          : t("statusUnknown")}
      </p>
      {parents.map((parent) => (
        <button
          key={parent.hypothesis_id}
          type="button"
          onClick={() => onInspect(parent)}
          className="mr-3 text-cyan hover:underline"
        >
          {t("parentHypothesis", {
            generation: parent.generation,
            number: parent.slot + 1,
          })}{" "}
          →
        </button>
      ))}
      {parents.length < new Set(hypothesis.parent_ids ?? []).size && (
        <p>{t("parentsUnavailable")}</p>
      )}
    </div>
  );
}
