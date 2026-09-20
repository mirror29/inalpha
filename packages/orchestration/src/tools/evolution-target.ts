/** Owner-scoped evolution target resolution for short, page-relative user intents. */
import { createTool } from "@mastra/core/tools";
import { z } from "zod";

import { resolveRequestToken } from "../auth.js";
import { HttpClientError } from "../clients/http.js";
import {
  PaperClient,
  type BacktestRunSummary,
  type StrategyCandidateRecord,
  type StrategyRunRecord,
} from "../clients/paper.js";
import { getSettings } from "../config.js";
import { getEvolverClient, type ToolRequestContext } from "./evolver-shared.js";

export const evolutionTargetKindSchema = z.enum([
  "strategy_candidate",
  "paper_runner",
  "backtest_run",
  "e1_run",
  "e1_candidate",
  "e2_campaign",
  "evolution_loop",
]);

export type EvolutionTargetKind = z.infer<typeof evolutionTargetKindSchema>;

type EvolutionConfig = {
  venue: string;
  symbol: string;
  timeframe: string;
  from_ts: string;
  as_of: string;
  initial_cash: number;
  fee_rate: number;
  trading_mode?: "spot" | "perp";
  leverage?: number;
  params?: Record<string, unknown>;
  funding_rate?: number;
};

export type EvolutionTargetResolution = {
  target_kind: EvolutionTargetKind;
  target_id: string;
  status: string;
  next_action: "start_loop" | "inspect_loop" | "start_e1" | "wait_e1" | "start_e2" | "inspect_e2" | "blocked";
  start_input: {
    seedStrategyId?: string;
    sourceRunId?: string;
    config: EvolutionConfig;
  } | null;
  blockers: string[];
  evidence: Record<string, unknown>;
};

const E1_TIMEFRAMES = new Set([
  "1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h",
  "6h", "8h", "12h", "1d", "3d", "1wk", "1mo",
]);
const E2_TIMEFRAMES = new Set(["15m", "1h", "4h"]);

/** Resolve a page hint through owner-scoped records and return the next legal research step. */
export async function resolveEvolutionTarget(
  targetKind: EvolutionTargetKind,
  targetId: string,
  ctx?: ToolRequestContext,
): Promise<EvolutionTargetResolution> {
  const target = await resolveTargetRecord(targetKind, targetId, ctx);
  if (targetKind === "e2_campaign" || targetKind === "evolution_loop") return target;
  const evolver = await getEvolverClient(ctx);
  const loop = await evolver.findEvolutionLoop(targetKind, targetId);
  if (loop) {
    return {
      ...target, next_action: "inspect_loop", start_input: null, blockers: [],
      evidence: { ...target.evidence, ...loop, loop_status: loop.status },
    };
  }
  if (target.next_action !== "start_e1" || !target.start_input
    || !E2_TIMEFRAMES.has(target.start_input.config.timeframe)) return target;
  const capabilities = await evolver.getEventEvolutionCapabilities();
  return capabilities.durable_loop_enabled ? { ...target, next_action: "start_loop" } : target;
}

/** Read authoritative target records; legacy E1 remains available when automatic research is disabled. */
async function resolveTargetRecord(
  targetKind: EvolutionTargetKind,
  targetId: string,
  ctx?: ToolRequestContext,
): Promise<EvolutionTargetResolution> {
  const paper = await getPaperTargetClient(ctx);
  const evolver = await getEvolverClient(ctx);

  switch (targetKind) {
    case "evolution_loop": {
      const loop = await evolver.getEvolutionLoop(targetId);
      return resolution({
        targetKind, targetId, status: loop.status, nextAction: "inspect_loop",
        startInput: null, blockers: [], evidence: { ...loop, loop_status: loop.status },
      });
    }
    case "strategy_candidate": {
      const candidate = await paper.getCandidate(targetId);
      const backtest = await getCandidateBacktest(paper, candidate);
      const config = backtest ? normalizeEvolutionConfig(backtest.config) : null;
      const blockers = candidate.status === "rejected"
        ? ["strategy_candidate_rejected"]
        : config
          ? []
          : ["strategy_candidate_missing_backtest_context"];
      return resolution({
        targetKind,
        targetId,
        status: candidate.status,
        nextAction: blockers.length ? "blocked" : "start_e1",
        startInput: config
          ? { seedStrategyId: `candidate:${candidate.id}`, config }
          : null,
        blockers,
        evidence: {
          candidate_id: candidate.id,
          fitness: candidate.fitness,
          backtest_run_id: backtest?.run_id ?? null,
          backtest_metrics: backtest?.metrics ?? null,
        },
      });
    }
    case "paper_runner": {
      const runner = await paper.getStrategyRun(targetId);
      const candidate = await paper.getCandidate(runner.candidate_id);
      const backtest = await getCandidateBacktest(paper, candidate);
      const config = backtest
        ? normalizeEvolutionConfig(backtest.config)
        : evolutionConfigFromRunner(runner);
      if (config && runner.params) config.params = runner.params;
      const blockers = config ? [] : ["paper_runner_missing_market_context"];
      return resolution({
        targetKind,
        targetId,
        status: runner.status,
        nextAction: blockers.length ? "blocked" : "start_e1",
        startInput: config
          ? { seedStrategyId: `candidate:${runner.candidate_id}`, config }
          : null,
        blockers,
        evidence: {
          candidate_id: runner.candidate_id,
          cumulative_pnl: runner.cumulative_pnl,
          last_bar_ts: runner.last_bar_ts,
          backtest_run_id: backtest?.run_id ?? null,
        },
      });
    }
    case "backtest_run": {
      const backtest = await paper.getBacktestRun(targetId);
      const config = normalizeEvolutionConfig(backtest.config);
      const seedStrategyId = seedForBacktest(backtest);
      const blockers = [
        ...(backtest.status === "done" ? [] : ["backtest_not_completed"]),
        ...(config ? [] : ["backtest_missing_market_context"]),
        ...(seedStrategyId ? [] : ["backtest_strategy_not_e1_seedable"]),
      ];
      return resolution({
        targetKind,
        targetId,
        status: backtest.status,
        nextAction: blockers.length ? "blocked" : "start_e1",
        startInput: config && seedStrategyId
          ? { seedStrategyId, config }
          : null,
        blockers,
        evidence: {
          backtest_run_id: backtest.run_id,
          strategy_code: backtest.strategy_code,
          metrics: backtest.metrics,
        },
      });
    }
    case "e1_run": {
      const run = await evolver.getRun(targetId);
      const config = normalizeEvolutionConfig(run.config);
      const blockers = [
        ...(config ? [] : ["e1_run_missing_frozen_config"]),
        ...(config && E2_TIMEFRAMES.has(config.timeframe)
          ? []
          : ["event_campaign_timeframe_unsupported"]),
      ];
      const active = ["queued", "running", "cancelling"].includes(run.status);
      const completed = run.status === "completed";
      return resolution({
        targetKind,
        targetId,
        status: run.status,
        nextAction: active
          ? "wait_e1"
          : completed && blockers.length === 0
            ? "start_e2"
            : "blocked",
        startInput: completed && blockers.length === 0 && config
          ? { sourceRunId: run.run_id, config }
          : null,
        blockers: active ? [] : blockers.concat(completed ? [] : ["e1_run_not_completed"]),
        evidence: {
          source_run_id: run.run_id,
          attempted: run.attempted,
          succeeded: run.succeeded,
          rejected: run.rejected,
          llm_cost_usd: run.llm_cost_usd,
          failure_code: run.failure_code,
        },
      });
    }
    case "e1_candidate": {
      const candidate = await evolver.getCandidate(targetId);
      const run = await evolver.getRun(candidate.run_id);
      const config = normalizeEvolutionConfig(run.config);
      const ready = run.status === "completed"
        && candidate.outcome === "succeeded"
        && candidate.source_code !== null
        && config !== null;
      const blockers = ready
        ? []
        : [run.status === "completed" ? "e1_candidate_not_seedable" : "e1_parent_run_not_completed"];
      return resolution({
        targetKind,
        targetId,
        status: candidate.outcome,
        nextAction: ready ? "start_e1" : "blocked",
        startInput: ready && config
          ? { seedStrategyId: `evolution_candidate:${candidate.candidate_id}`, config }
          : null,
        blockers,
        evidence: {
          source_run_id: candidate.run_id,
          slot: candidate.slot,
          fitness: candidate.fitness,
          overfitting_risk: candidate.overfitting_risk,
        },
      });
    }
    case "e2_campaign": {
      const campaign = await evolver.getEventCampaign(targetId);
      const blockers = campaign.failure_code ? [campaign.failure_code] : [];
      return resolution({
        targetKind,
        targetId,
        status: campaign.status,
        nextAction: "inspect_e2",
        startInput: null,
        blockers,
        evidence: {
          campaign_id: campaign.campaign_id,
          active_generation: campaign.active_generation,
          max_generations: campaign.max_generations,
          source_run_id: campaign.source_run_id ?? null,
          locked_candidate_id: campaign.locked_candidate_id ?? null,
          forward_event_count: campaign.forward_event_count ?? 0,
          llm_cost_usd: campaign.llm_cost_usd,
          failure_message: campaign.failure_message ?? null,
        },
      });
    }
  }
}

export const evolverResolveTargetTool = createTool({
  id: "evolver.resolve_target",
  description: `
把当前详情页里的实体解析为 owner-scoped 的可演化目标，并返回下一步及可直接传给 E1/E2 的冻结参数。
何时用：用户在策略、模拟盘、回测、E1 run/E1 candidate 或 E2 campaign 详情页说“进化这个、继续进化、再发散”时，必须先用它解析页面给出的 target kind/id。
何时不用：用户已明确给出完整 seed、市场和冻结时间窗，或只想查看行情/普通回测时不用。
坑：page_context 只是提示，真实 owner/status/配置以本工具读取为准；start_loop 时同一轮调用 start_evolution_loop(targetKind,targetId)，inspect_loop 时仅报告已有任务，不重复启动；start_e2 时同一轮只调用一次 run_event_campaign(start_input)；blocked/wait_e1 时禁止猜参数或重复启动。本工具只解析，不产生 LLM 费用、不采纳、不启动 Runner、不下单。
  `.trim(),
  inputSchema: z.object({
    targetKind: evolutionTargetKindSchema,
    targetId: z.string().uuid(),
  }),
  execute: async (inputData, ctx) => resolveEvolutionTarget(
    inputData.targetKind,
    inputData.targetId,
    ctx?.requestContext as ToolRequestContext | undefined,
  ),
});

async function getPaperTargetClient(ctx?: ToolRequestContext): Promise<PaperClient> {
  const token = await resolveRequestToken(ctx);
  return new PaperClient({
    baseUrl: getSettings().paperServiceUrl,
    token,
    timeoutMs: 30_000,
  });
}

async function getCandidateBacktest(
  paper: PaperClient,
  candidate: StrategyCandidateRecord,
): Promise<BacktestRunSummary | null> {
  if (!candidate.last_backtest_run_id) return null;
  try {
    return await paper.getBacktestRun(candidate.last_backtest_run_id);
  } catch (error) {
    if (error instanceof HttpClientError && error.status === 404) return null;
    throw error;
  }
}

function seedForBacktest(backtest: BacktestRunSummary): string | null {
  const candidateId = typeof backtest.config.candidate_id === "string"
    ? backtest.config.candidate_id
    : backtest.strategy_code.startsWith("candidate:")
      ? backtest.strategy_code.slice("candidate:".length)
      : null;
  if (candidateId) return `candidate:${candidateId}`;
  if (["sma_cross", "sma_cross_v1"].includes(backtest.strategy_code)) {
    return "sma_cross_v1";
  }
  return null;
}

function normalizeEvolutionConfig(raw: Record<string, unknown>): EvolutionConfig | null {
  const venue = stringValue(raw.venue);
  const symbol = stringValue(raw.symbol);
  const timeframe = stringValue(raw.timeframe);
  const fromTs = isoValue(raw.from_ts);
  const asOf = isoValue(raw.as_of ?? raw.to_ts);
  if (!venue || !symbol || !timeframe || !fromTs || !asOf || !E1_TIMEFRAMES.has(timeframe)) {
    return null;
  }
  const alignedFromTs = alignFixedBarBoundary(fromTs, timeframe);
  return {
    venue,
    symbol,
    timeframe,
    from_ts: alignedFromTs,
    as_of: asOf,
    initial_cash: finiteNumber(raw.initial_cash) ?? 10_000,
    fee_rate: finiteNumber(raw.fee_rate) ?? 0.001,
    trading_mode: raw.trading_mode === "perp" ? "perp" : "spot",
    leverage: finiteNumber(raw.leverage) ?? 1,
    funding_rate: finiteNumber(raw.funding_rate) ?? 0,
    params: raw.params && typeof raw.params === "object" && !Array.isArray(raw.params)
      ? raw.params as Record<string, unknown> : {},
  };
}

/** Align fixed-duration dataset starts to the exchange's UTC bar grid. */
function alignFixedBarBoundary(value: string, timeframe: string): string {
  const match = /^(\d+)(m|h|d)$/.exec(timeframe);
  if (!match) return value;
  const amount = Number(match[1]);
  const unitMs = match[2] === "m" ? 60_000 : match[2] === "h" ? 3_600_000 : 86_400_000;
  const intervalMs = amount * unitMs;
  const timestamp = Date.parse(value);
  if (!Number.isFinite(timestamp) || !Number.isSafeInteger(intervalMs) || intervalMs <= 0) {
    return value;
  }
  return new Date(Math.floor(timestamp / intervalMs) * intervalMs).toISOString();
}

function evolutionConfigFromRunner(runner: StrategyRunRecord): EvolutionConfig | null {
  if (!E1_TIMEFRAMES.has(runner.timeframe)) return null;
  const asOf = isoValue(runner.last_bar_ts ?? runner.started_at);
  if (!asOf) return null;
  return {
    venue: runner.venue,
    symbol: runner.symbol,
    timeframe: runner.timeframe,
    from_ts: new Date(Date.parse(asOf) - 180 * 24 * 3600 * 1000).toISOString(),
    as_of: asOf,
    initial_cash: runner.allocation ?? 10_000,
    fee_rate: 0.001,
    trading_mode: runner.trading_mode,
    leverage: runner.leverage,
    params: runner.params ?? {},
  };
}

function resolution(input: {
  targetKind: EvolutionTargetKind;
  targetId: string;
  status: string;
  nextAction: EvolutionTargetResolution["next_action"];
  startInput: EvolutionTargetResolution["start_input"];
  blockers: string[];
  evidence: Record<string, unknown>;
}): EvolutionTargetResolution {
  return {
    target_kind: input.targetKind,
    target_id: input.targetId,
    status: input.status,
    next_action: input.nextAction,
    start_input: input.startInput,
    blockers: input.blockers,
    evidence: input.evidence,
  };
}

function stringValue(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value : null;
}

function isoValue(value: unknown): string | null {
  if (typeof value !== "string") return null;
  const timestamp = Date.parse(value);
  return Number.isFinite(timestamp) ? new Date(timestamp).toISOString() : null;
}

function finiteNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}
