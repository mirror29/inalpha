/** Evolver Mastra tools：显式发起、查询、候选详情与取消。 */
import { createTool } from "@mastra/core/tools";
import { z } from "zod";

import { mintServiceToken, resolveRequestSubject } from "../auth.js";
import { DataClient, type AssetIdentity, type MarketEventType } from "../clients/data.js";
import { getSettings } from "../config.js";
import {
  evolutionConfigSchema,
  eventCampaignConfigSchema,
  getApprovedEventCampaignContext,
  getApprovedEvolutionRunContext,
  getEvolverClient,
  type ToolRequestContext,
} from "./evolver-shared.js";

const AUTOMATIC_SNAPSHOT_POLICY = "all-visible-facts-v1";
const AUTOMATIC_EVENT_TYPES: MarketEventType[] = [
  "listing",
  "delisting",
  "exploit",
  "chain_halt",
];

export async function createAutomaticEventSnapshot(
  config: {
    venue: string;
    symbol: string;
    as_of: string;
  },
  ctx?: ToolRequestContext,
  existingSnapshotId?: string,
): Promise<{ snapshotId: string; asset: AssetIdentity }> {
  const owner = await resolveRequestSubject(ctx);
  const resolveToken = await mintServiceToken({
    sub: owner,
    token_use: "service",
    service_audience: "data",
    token_purpose: "asset_resolve",
  }, 300);
  const asset = await new DataClient({
    baseUrl: getSettings().dataServiceUrl,
    token: resolveToken,
    timeoutMs: 30_000,
  }).resolveAsset({ venue: config.venue, symbol: config.symbol });
  if (existingSnapshotId) return { snapshotId: existingSnapshotId, asset };
  const snapshotToken = await mintServiceToken({
    sub: owner,
    token_use: "service",
    service_audience: "data",
    token_purpose: "event_snapshot_create",
  }, 300);
  const client = new DataClient({
    baseUrl: getSettings().dataServiceUrl,
    token: snapshotToken,
    timeoutMs: 30_000,
  });
  const snapshot = await client.createEventSnapshot({
    cutoff: config.as_of,
    policyVersion: AUTOMATIC_SNAPSHOT_POLICY,
    eventTypes: AUTOMATIC_EVENT_TYPES,
    assets: [asset.event_asset_code],
    assetIds: [asset.asset_id],
  });
  if (snapshot.fact_count < 1) {
    throw new Error(
      "EVENT_SNAPSHOT_EMPTY: no point-in-time event facts cover this asset and cutoff; " +
        "backfill the event ledger before starting the campaign",
    );
  }
  return { snapshotId: snapshot.snapshot_id, asset };
}

export const evolverRunEventCampaignTool = createTool({
  id: "evolver.run_event_campaign",
  description: `
基于冻结事件事实与模拟盘反馈启动五代双层自动演化；每代两个 Agent proposer 产生八个假设，每个确定性展开三条实现，并锁定唯一冠军等待独立 Forward。
何时用：用户要求从事件机制发散新策略方向；用户要求基于某次模拟结果继续进化时，必须传入该次已完成 E1 run 的 sourceRunId。eventSnapshotId 可省略，系统会按 as_of 和标的自动冻结可见事件。
何时不用：只优化既有代码用 evolver.run_evolution；需要实盘或希望自动下单时不要用。
坑：owner 的点击或明确指令会直接启动研究型 campaign；operation 与 grant 仍绑定 owner、冻结模型和请求摘要。自动快照没有事件时会在产生 LLM 费用前失败；内部自动迭代不逐代审批，不会采纳、启动 Runner 或下单。
  `.trim(),
  inputSchema: z.object({
    eventSnapshotId: z.string().uuid().optional(),
    sourceRunId: z.string().uuid().optional().describe("需要作为第一代进化反馈的已完成、同 owner、同市场 E1 run ID"),
    targetKind: z.enum([
      "strategy_candidate", "paper_runner", "backtest_run", "e1_run", "e1_candidate", "e2_campaign",
    ]).optional(),
    targetId: z.string().min(1).max(200).optional(),
    config: eventCampaignConfigSchema,
  }).refine(
    (value) => (value.targetKind === undefined) === (value.targetId === undefined),
    { message: "targetKind and targetId must be provided together" },
  ),
  execute: async (inputData, ctx) => {
    const requestContext = ctx?.requestContext as ToolRequestContext | undefined;
    const client = await getEvolverClient(requestContext);
    const capability = await client.getEventEvolutionCapabilities();
    if (!capability.event_evolution_enabled) {
      throw new Error(
        `EVENT_EVOLUTION_DISABLED: ${capability.reason ?? "server capability is disabled"}`,
      );
    }
    const frozen = await createAutomaticEventSnapshot(
      inputData.config,
      requestContext,
      inputData.eventSnapshotId,
    );
    const eventSnapshotId = frozen.snapshotId;
    const approved = await getApprovedEventCampaignContext(
      {
        ...inputData,
        eventSnapshotId,
        targetKind: inputData.targetKind ?? (inputData.sourceRunId ? "e1_run" : undefined),
        targetId: inputData.targetId ?? inputData.sourceRunId,
        config: {
          ...inputData.config,
          asset_id: frozen.asset.asset_id,
          event_asset_code: frozen.asset.event_asset_code,
        },
      },
      requestContext,
    );
    return await approved.client.startEventCampaign({
      request: approved.request,
      idempotencyKey: approved.operationId,
      credentialGrant: approved.credentialGrant,
    });
  },
});

export const evolverGetEventCampaignTool = createTool({
  id: "evolver.get_event_campaign",
  description: "查询本人事件演化 campaign 的代际、Forward、holdout 与费用状态；不用它读取原始新闻或普通 E1 run。",
  inputSchema: z.object({ campaignId: z.string().uuid() }),
  execute: async (inputData, ctx) =>
    await (await getEvolverClient(ctx?.requestContext as ToolRequestContext | undefined)).getEventCampaign(inputData.campaignId),
});

export const evolverRunEvolutionTool = createTool({
  id: "evolver.run_evolution",
  description: `
启动一次真实数据驱动的单代策略演化。每个候选经过源码审计、契约校验和同一冻结数据集回测；调用会产生 LLM 与计算成本，必须获得用户确认。
何时用：用户明确要求变异、优化或探索已存在策略的新方向。
何时不用：只需一次回测时用 paper.run_backtest；种子未验证或用户未授权额外成本时不要调用。
坑：返回 queued run_id 后用 evolver.get_evolution 轮询；freshness 不足会 fail closed；不会自动 promote、注册或启动候选。
  `.trim(),
  inputSchema: z.object({
    budget: z.number().int().min(1).max(20).default(4),
    seedStrategyId: z.string().min(1).max(128).default("sma_cross_v1"),
    config: evolutionConfigSchema,
  }),
  execute: async (inputData, ctx) => {
    const approved = await getApprovedEvolutionRunContext(
      inputData,
      ctx?.requestContext as ToolRequestContext | undefined,
    );
    return await approved.client.startRun({
      request: approved.request,
      idempotencyKey: approved.operationId,
      credentialGrant: approved.credentialGrant,
    });
  },
});

export const evolverGetEvolutionTool = createTool({
  id: "evolver.get_evolution",
  description: `
查询演化 run 的状态、数据 manifest 和已完成 slot。
何时用：轮询 queued/running/cancelling run，或复核 completed/failed/aborted 历史。
何时不用：只看单个候选完整源码和 diff 时用 evolver.get_candidate。
坑：跨用户资源统一返回 404；terminal 状态无需继续轮询。
  `.trim(),
  inputSchema: z.object({ runId: z.string().uuid() }),
  execute: async (inputData, ctx) =>
    await (await getEvolverClient(ctx?.requestContext as ToolRequestContext | undefined)).getRun(inputData.runId),
});

export const evolverGetCandidateTool = createTool({
  id: "evolver.get_candidate",
  description: "查询当前用户拥有的单个演化候选源码、diff、审计与真实回测快照；不用它列 run。",
  inputSchema: z.object({ candidateId: z.string().uuid() }),
  execute: async (inputData, ctx) =>
    await (await getEvolverClient(ctx?.requestContext as ToolRequestContext | undefined)).getCandidate(inputData.candidateId),
});

export const evolverAbortEvolutionTool = createTool({
  id: "evolver.abort_evolution",
  description: "取消 queued/running 演化 run；保留已完成 slot。仅在用户明确要求停止时使用，terminal run 无需调用。",
  inputSchema: z.object({ runId: z.string().uuid() }),
  execute: async (inputData, ctx) =>
    await (await getEvolverClient(ctx?.requestContext as ToolRequestContext | undefined)).abortRun(inputData.runId),
});

export const evolverTools = [
  evolverRunEventCampaignTool,
  evolverGetEventCampaignTool,
  evolverRunEvolutionTool,
  evolverGetEvolutionTool,
  evolverGetCandidateTool,
  evolverAbortEvolutionTool,
] as const;
