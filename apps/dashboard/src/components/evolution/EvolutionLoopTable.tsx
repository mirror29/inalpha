"use client";

import { useLocale, useTranslations } from "next-intl";

import { Link } from "@/i18n/navigation";
import { fmtRelative } from "@/lib/format";
import type { EvolutionLoop, EvolutionLoopStatus } from "@/lib/types";
import { Panel } from "@/components/ui/Panel";
import { StatusBadge } from "@/components/ui/StatusBadge";
import { Td, TableEmpty, TableHeadRow, Th } from "@/components/ui/Table";

/** Display the durable end-to-end workflow rather than exposing each subsystem as a separate task. */
export function EvolutionLoopTable({ loops }: { loops: EvolutionLoop[] }) {
  const t = useTranslations("evolution.loop");
  const locale = useLocale();
  return (
    <Panel title={t("title")} aside={<span className="font-mono text-xs text-fg-muted">{loops.length}</span>}>
      {loops.length === 0 ? (
        <TableEmpty>{t("empty")}</TableEmpty>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full border-collapse text-sm">
            <thead><TableHeadRow><Th>{t("target")}</Th><Th>{t("status")}</Th><Th>{t("campaign")}</Th><Th>{t("forward")}</Th><Th>{t("holdout")}</Th><Th>{t("updated")}</Th></TableHeadRow></thead>
            <tbody>
              {loops.map((loop) => (
                <tr key={loop.loop_id} className="border-t border-border-subtle/60 hover:bg-bg-elev/30">
                  <Td mono>
                      <Link href={`/evolution/loops/${loop.loop_id}`} className="text-cyan hover:underline">
                        {loop.target_kind}:{loop.target_id.slice(0, 12)}
                      </Link>
                  </Td>
                  <Td><StatusBadge label={t(`states.${loop.status}`)} tone={loopTone(loop.status)} dot pulse={isLoopActive(loop.status)} /></Td>
                  <Td mono muted>{loop.campaign_id?.slice(0, 8) ?? t("pending")}</Td>
                  <Td mono muted>{loop.forward_sandbox_id ? t("created") : t("pending")}</Td>
                  <Td mono muted>{loop.holdout_attempt_id ? t("consumed") : t("sealed")}</Td>
                  <Td><span className="font-mono text-[11px] text-fg-muted">{fmtRelative(loop.updated_at, Date.now(), locale)}</span></Td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Panel>
  );
}

/** Return whether the reconciler is expected to keep advancing this loop. */
function isLoopActive(status: EvolutionLoopStatus) {
  return ["target_resolved", "baseline_ready", "campaign_running", "candidate_locked", "waiting_forward", "holdout_running"].includes(status);
}

/** Map workflow state to the shared semantic color language. */
function loopTone(status: EvolutionLoopStatus) {
  if (status === "adoption_ready") return "bull" as const;
  if (status === "campaign_running" || status === "holdout_running") return "cyan" as const;
  if (status === "candidate_locked" || status === "waiting_forward") return "gold" as const;
  if (status === "failed" || status === "rejected") return "fox" as const;
  return "muted" as const;
}
