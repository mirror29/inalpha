"use client";

import { useLocale, useTranslations } from "next-intl";

import { Link } from "@/i18n/navigation";
import { fmtRelative } from "@/lib/format";
import type { EvolutionCampaign } from "@/lib/types";
import { Panel } from "@/components/ui/Panel";
import { StatusBadge } from "@/components/ui/StatusBadge";
import { Td, TableEmpty, TableHeadRow, Th } from "@/components/ui/Table";

/** Compact campaign read model for the primary evolution workspace. */
export function EvolutionCampaignTable({ campaigns }: { campaigns: EvolutionCampaign[] }) {
  const t = useTranslations("evolution.campaign");
  const locale = useLocale();
  return (
    <Panel title={t("title")} aside={<span className="font-mono text-xs text-fg-muted">{campaigns.length}</span>}>
      {campaigns.length === 0 ? (
        <TableEmpty>{t("empty")}</TableEmpty>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full border-collapse text-sm">
            <thead><TableHeadRow><Th>{t("id")}</Th><Th>{t("status")}</Th><Th>{t("stage")}</Th><Th right>{t("budget")}</Th><Th right>{t("events")}</Th><Th>{t("holdout")}</Th><Th>{t("updated")}</Th></TableHeadRow></thead>
            <tbody>
              {campaigns.map((campaign) => (
                <tr key={campaign.campaign_id} className="border-t border-border-subtle/60 hover:bg-bg-elev/30">
                  <Td mono><Link href={`/evolution/campaigns/${campaign.campaign_id}`} className="text-cyan hover:underline">{campaign.campaign_id.slice(0, 8)}</Link></Td>
                  <Td><StatusBadge label={campaign.status} tone={campaignTone(campaign.status)} dot pulse={campaign.status === "replaying"} /></Td>
                  <Td mono muted>{campaign.active_generation}/{campaign.max_generations}</Td>
                  <Td right mono muted>{campaign.hypothesis_budget * campaign.implementations_per_hypothesis}</Td>
                  <Td right mono muted>{campaign.forward_event_count}</Td>
                  <Td><span className="font-mono text-[11px] text-fg-muted">{campaign.holdout_consumed_at ? t("consumed") : t("sealed")}</span></Td>
                  <Td><span className="font-mono text-[11px] text-fg-muted">{fmtRelative(campaign.updated_at, Date.now(), locale)}</span></Td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Panel>
  );
}

/** Map campaign state to one consistent semantic dashboard tone. */
function campaignTone(status: EvolutionCampaign["status"]) {
  if (status === "graduated") return "bull" as const;
  if (status === "replaying" || status === "holdout_ready") return "cyan" as const;
  if (status === "waiting_forward" || status === "candidate_locked") return "gold" as const;
  if (status === "failed" || status === "rejected") return "fox" as const;
  return "muted" as const;
}
