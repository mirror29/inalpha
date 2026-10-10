/** Prepare explicit research windows using normal owner identity, without a model call. */
import type { Handler } from "hono";
import { z } from "zod";
import { verifyToken } from "../auth.js";
import { HttpClientError } from "../clients/http.js";
import { resolveEvolutionTarget } from "../tools/evolution-target.js";
import { createAutomaticEventSnapshot } from "../tools/evolver.js";
import { getEvolverClient, type ToolRequestContext } from "../tools/evolver-shared.js";

export const experimentInputSchema = z.object({
  targetKind: z.enum(["strategy_candidate", "paper_runner", "backtest_run", "e1_candidate"]),
  targetId: z.string().uuid(),
  window: z.object({ from_ts: z.string().datetime({ offset: true }), as_of: z.string().datetime({ offset: true }) }).optional(),
}).strict();

/** Suggest a chronological window from availability only; never inspect returns or optimize selection. */
export function suggestExperimentWindow(facts: Array<Record<string, unknown>>, timeframe: string, now: number) {
  const step = ({ "15m": 900_000, "1h": 3_600_000, "4h": 14_400_000 } as Record<string, number>)[timeframe];
  if (!step) throw new Error("E2_TIMEFRAME_UNSUPPORTED");
  const end = Math.floor(now / step) * step;
  const times = facts.map(fact => typeof fact.available_at === "string" ? Date.parse(fact.available_at) : NaN)
    .filter(value => Number.isFinite(value) && value <= end);
  if (!times.length) return null;
  const start = Math.floor(times.reduce((earliest, value) => Math.min(earliest, value), Infinity) / step) * step;
  if (start >= end) return null;
  return { from_ts: new Date(start).toISOString(), as_of: new Date(end).toISOString() };
}

/** Read the owned seed before any Data request; preparing creates no execution authority. */
export async function prepareExperiment(input: z.infer<typeof experimentInputSchema>, rc: ToolRequestContext, existingSnapshotId?: string) {
  const target = await resolveEvolutionTarget(input.targetKind, input.targetId, rc);
  if (target.next_action === "inspect_loop") return { status: "existing_experiment", target, execution_authorized: false };
  if (target.next_action !== "start_loop" || !target.start_input?.seedStrategyId) {
    return { status: "blocked", blocker_codes: target.blockers, target, execution_authorized: false };
  }
  const inherited = target.start_input.config;
  const step = ({ "15m": 900_000, "1h": 3_600_000, "4h": 14_400_000 } as Record<string, number>)[inherited.timeframe];
  if (!step) throw new Error("E2_TIMEFRAME_UNSUPPORTED");
  const cutoff = input.window?.as_of ?? new Date(Math.floor(Date.now() / step) * step).toISOString();
  if (Date.parse(cutoff) > Date.now() || (input.window && Date.parse(input.window.from_ts) >= Date.parse(cutoff))) {
    throw new Error("EXPERIMENT_WINDOW_INVALID");
  }
  const frozen = await createAutomaticEventSnapshot({ ...inherited, as_of: cutoff }, rc, existingSnapshotId, true);
  const window = input.window ?? suggestExperimentWindow(frozen.facts ?? [], inherited.timeframe, Date.parse(cutoff));
  if (!window) return { status: "blocked", blocker_codes: ["EVENT_SNAPSHOT_EMPTY"], target, model_calls: 0, execution_authorized: false };
  const config = { ...inherited, ...window };
  const preflight = await (await getEvolverClient(rc)).prepareExperiment({
    target_kind: input.targetKind, target_id: input.targetId,
    seed_strategy_id: target.start_input.seedStrategyId,
    event_snapshot_id: frozen.snapshotId, event_asset_code: frozen.asset.event_asset_code, config,
  });
  return {
    ...preflight, target, config, window_source: input.window ? "explicit" : "first_available_event",
    inherited_window: { from_ts: inherited.from_ts, as_of: inherited.as_of },
    model_calls: 0, execution_authorized: false,
  };
}

/** Reject missing/invalid owners even when middleware is absent; never expose raw backend errors. */
export const prepareExperimentHandler: Handler = async c => {
  const token = c.req.header("Authorization")?.match(/^Bearer\s+(.+)$/i)?.[1];
  if (!token) return c.json({ error: "unauthorized" }, 401);
  try {
    const owner = await verifyToken(token);
    if (!owner.sub || owner.token_use !== undefined) return c.json({ error: "unauthorized" }, 401);
  } catch { return c.json({ error: "unauthorized" }, 401); }
  const input = experimentInputSchema.safeParse(await c.req.json().catch(() => null));
  if (!input.success) return c.json({ error: "EXPERIMENT_INPUT_INVALID" }, 400);
  if (input.data.window && (Date.parse(input.data.window.as_of) > Date.now()
    || Date.parse(input.data.window.from_ts) >= Date.parse(input.data.window.as_of))) {
    return c.json({ error: "EXPERIMENT_WINDOW_INVALID" }, 400);
  }
  try {
    c.header("Cache-Control", "no-store");
    return c.json(await prepareExperiment(input.data, { authToken: token }));
  } catch (error) {
    if (error instanceof HttpClientError && ["EVOLUTION_DATA_GAP_INVALID", "EVOLUTION_DATA_LIMIT_EXCEEDED", "EVOLUTION_DATA_RANGE_INVALID", "EVOLUTION_DATA_STALE"].includes(error.code)) {
      return c.json({ status: "blocked", blocker_codes: [error.code], model_calls: 0, execution_authorized: false });
    }
    return c.json({ error: "EXPERIMENT_PREPARATION_UNAVAILABLE" }, error instanceof HttpClientError && error.status === 404 ? 404 : 502);
  }
};

export const experimentApiRoutes = [{ path: "/evolution/prepare", method: "POST" as const, handler: prepareExperimentHandler }];
