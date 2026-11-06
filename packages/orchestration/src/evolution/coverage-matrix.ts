/** Compare owned research inputs at one sampling time without ranking returns. */
import { mintServiceToken, resolveRequestSubject, resolveRequestToken } from "../auth.js";
import { DataClient } from "../clients/data.js";
import { PaperClient } from "../clients/paper.js";
import { HttpClientError } from "../clients/http.js";
import { getSettings } from "../config.js";
import type { ToolRequestContext } from "../tools/evolver-shared.js";
import { EXPERIMENT_DATA_BLOCKERS, prepareExperiment } from "./preparation.js";

const ASSETS = ["BTC", "ETH", "SOL"] as const;
export type CoverageRow = {
  asset: typeof ASSETS[number];
  target_id: string | null;
  status: string;
  independent_event_upper_bound?: number;
  minimum_matched_event_pairs?: number;
  blocker_codes?: string[];
  preparation?: Record<string, unknown>;
};

/** Only input-ready rows qualify; deterministic ties never inspect fitness or PnL. */
export function selectCoverageTarget(rows: CoverageRow[]): string | null {
  return [...rows].filter(row => row.status === "necessary_inputs_present" && row.target_id
    && Number.isFinite(row.independent_event_upper_bound)
    && Number.isFinite(row.minimum_matched_event_pairs) && row.minimum_matched_event_pairs! > 0
    && row.independent_event_upper_bound! >= row.minimum_matched_event_pairs!)
    .sort((a, b) => b.independent_event_upper_bound! - a.independent_event_upper_bound!
      || ASSETS.indexOf(a.asset) - ASSETS.indexOf(b.asset))[0]?.target_id ?? null;
}

/** Inspect the oldest running supported runner per asset, with stable ID ties and bounded work. */
export async function prepareCoverageMatrix(rc: ToolRequestContext) {
  const sampledAt = Date.now();
  const token = await resolveRequestToken(rc);
  const paper = new PaperClient({ baseUrl: getSettings().paperServiceUrl, token, timeoutMs: 30_000 });
  const runners = await paper.listStrategyRuns({ status: "running", limit: 200 });
  // The owner list is capped at 200; do not claim the oldest runner from a truncated inventory.
  if (runners.length >= 200) return {
    sampled_at: new Date(sampledAt).toISOString(), complete: false, recommended_target_id: null,
    rows: ASSETS.map(asset => ({ asset, target_id: null, status: "unavailable", blocker_codes: ["RUNNER_INVENTORY_INCOMPLETE"] })),
    model_calls: 0, execution_authorized: false,
  };
  const owner = await resolveRequestSubject(rc);
  const assetToken = await mintServiceToken({ sub: owner, token_use: "service", service_audience: "data", token_purpose: "asset_resolve" }, 300);
  const data = new DataClient({ baseUrl: getSettings().dataServiceUrl, token: assetToken, timeoutMs: 30_000 });
  const supported = runners.filter(run => run.status === "running" && ["15m", "1h", "4h"].includes(run.timeframe));
  const keys = [...new Map(supported.map(run => [JSON.stringify([run.venue, run.symbol]), { venue: run.venue, symbol: run.symbol }])).entries()];
  const assets = new Map<string, string>();
  // Resolve through Data's authoritative identity parser with at most four concurrent requests.
  for (let offset = 0; offset < keys.length; offset += 4) {
    const resolved = await Promise.allSettled(keys.slice(offset, offset + 4).map(async ([key, identity]) => {
      assets.set(key, (await data.resolveAsset(identity)).event_asset_code);
    }));
    if (resolved.some(result => result.status === "rejected")) return {
      sampled_at: new Date(sampledAt).toISOString(), complete: false, recommended_target_id: null,
      rows: ASSETS.map(asset => ({ asset, target_id: null, status: "unavailable", blocker_codes: ["ASSET_IDENTITY_UNAVAILABLE"] })),
      model_calls: 0, execution_authorized: false,
    };
  }
  const rows = await Promise.all(ASSETS.map(async (asset): Promise<CoverageRow> => {
    const runner = supported.filter(run => assets.get(JSON.stringify([run.venue, run.symbol])) === asset)
      .sort((a, b) => Date.parse(a.started_at) - Date.parse(b.started_at) || a.id.localeCompare(b.id))[0];
    if (!runner) return { asset, target_id: null, status: "blocked", blocker_codes: ["OWNED_RUNNER_UNAVAILABLE"] };
    try {
      const preparation: Record<string, unknown> = await prepareExperiment(
        { targetKind: "paper_runner", targetId: runner.id }, rc, undefined, sampledAt,
      );
      return { asset, target_id: runner.id, status: String(preparation.status),
        independent_event_upper_bound: typeof preparation.independent_event_upper_bound === "number"
          ? preparation.independent_event_upper_bound : undefined,
        minimum_matched_event_pairs: typeof preparation.minimum_matched_event_pairs === "number"
          ? preparation.minimum_matched_event_pairs : undefined,
        blocker_codes: Array.isArray(preparation.blocker_codes) ? preparation.blocker_codes as string[] : [],
        preparation };
    } catch (error) {
      const inputBlocked = error instanceof HttpClientError && EXPERIMENT_DATA_BLOCKERS.has(error.code);
      const code = inputBlocked ? error.code : "EXPERIMENT_PREPARATION_UNAVAILABLE";
      return { asset, target_id: runner.id, status: inputBlocked ? "blocked" : "unavailable", blocker_codes: [code] };
    }
  }));
  return { sampled_at: new Date(sampledAt).toISOString(), rows,
    complete: rows.every(row => row.status !== "unavailable"),
    recommended_target_id: rows.some(row => row.status === "unavailable") ? null : selectCoverageTarget(rows),
    selection_rule: "necessary_inputs_then_upper_bound_desc_then_BTC_ETH_SOL",
    target_rule: "oldest_running_supported_runner_per_asset_then_id",
    model_calls: 0, execution_authorized: false };
}
