import { isEvolutionActive } from "./evolution";
import { loopStage } from "./evolution-loop";
import type {
  EvolutionCampaign,
  EvolutionLoop,
  EvolutionRunSummary,
} from "./types";

/** Keep known workflow results separate from infrastructure failures. */
export function workflowState(status: string): string {
  return (
    (
      {
        draft: "target_resolved",
        replaying: "campaign_running",
        graduated: "adoption_ready",
      } as Record<string, string>
    )[status] ?? status
  );
}

/** Describe the frozen market without inventing a strategy name from an identifier. */
export function evolutionMarket(config: Record<string, unknown>): string {
  return [config.venue, config.symbol, config.timeframe]
    .filter((value) => typeof value === "string" && value)
    .join(" · ");
}

/** Show internal stage records once, under their durable parent workflow. */
export function independentExperiments(
  loops: EvolutionLoop[],
  campaigns: EvolutionCampaign[],
  runs: EvolutionRunSummary[],
) {
  const campaignIds = new Set(
    loops.map((loop) => loop.campaign_id).filter(Boolean),
  );
  const runIds = new Set(loops.map((loop) => loop.e1_run_id).filter(Boolean));
  campaigns.forEach((campaign) => {
    const source = campaign.frozen_config.source_run_id;
    if (typeof source === "string" && campaignIds.has(campaign.campaign_id))
      runIds.add(source);
  });
  return {
    campaigns: campaigns.filter(
      (campaign) => !campaignIds.has(campaign.campaign_id),
    ),
    runs: runs.filter((run) => !runIds.has(run.run_id)),
  };
}

/** Use the strategy's own first sentence as its title; keep its full description in evidence. */
export function strategyTitle(description?: string): string {
  return description?.trim().split(/[。\n]/, 1)[0] ?? "";
}

/** Count loaded experiments once; E1 completion never implies readiness for adoption. */
export function evolutionOverview(
  loops: EvolutionLoop[],
  campaigns: EvolutionCampaign[],
  runs: EvolutionRunSummary[],
) {
  const independent = independentExperiments(loops, campaigns, runs);
  return {
    independent,
    loadedTasks: loops.length + independent.campaigns.length + independent.runs.length,
    researching: loops.filter((loop) => {
      const stage = loopStage(loop.status);
      return stage !== null && stage !== 2 && stage !== 4;
    }).length + independent.runs.filter((run) => isEvolutionActive(run.status)).length
      + independent.campaigns.filter((campaign) => ["draft", "replaying"].includes(campaign.status)).length,
    observing: loops.filter((loop) => ["candidate_locked", "waiting_forward"].includes(loop.status)).length
      + independent.campaigns.filter((campaign) => ["candidate_locked", "waiting_forward"].includes(campaign.status)).length,
    ready: loops.filter((loop) => loop.status === "adoption_ready").length
      + independent.campaigns.filter((campaign) => campaign.status === "graduated").length,
  };
}
