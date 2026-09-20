/** One user intent starts a bounded research workflow; the chat is not its scheduler. */
import { createTool } from "@mastra/core/tools";
import { z } from "zod";

import { buildEventCampaignRequest, buildEvolutionStartRequest, evolutionLoopRequestDigest } from "../clients/evolver.js";
import { AUTH_SUB_KEY } from "../hooks/with-hooks.js";
import { mintEvolutionCredentialGrant } from "../mastra/llm/evolution-credential-grant.js";
import { APPROVAL_OPERATION_ID_KEY, getRequestContextValue, USER_LLM_SNAPSHOT_KEY, type EvolutionLLMSnapshot } from "../mastra/llm/evolution-snapshot.js";
import { resolveEvolutionTarget } from "./evolution-target.js";
import { createAutomaticEventSnapshot } from "./evolver.js";
import { eventCampaignConfigSchema, getEvolverClient, type ToolRequestContext } from "./evolver-shared.js";

export const evolverStartLoopTool = createTool({
  id: "evolver.start_evolution_loop",
  description: `从当前策略或模拟结果启动持久化 E1→E2→Forward→holdout 研究闭环。
何时用：用户明确要求“开始进化”或自动持续探索当前策略，且已有稳定 targetKind/targetId。
何时不用：只查状态、只做一次回测或只运行旧 E1；不支持实盘操作。
坑：仅在服务端 durable_loop_enabled 时可用；重复触发复用活动 Loop。费用受冻结预算上限约束，证据不足会结束；最终必须人工采用，绝不 promote、启动 Runner 或下单。`,
  inputSchema: z.object({
    targetKind: z.enum(["strategy_candidate", "paper_runner", "backtest_run", "e1_candidate"]),
    targetId: z.string().uuid(),
    budget: z.number().int().min(1).max(20).default(4),
    maxCostUsd: z.number().positive().max(100).optional(),
  }),
  execute: async (input, ctx) => {
    const rc = ctx?.requestContext as ToolRequestContext | undefined;
    const client = await getEvolverClient(rc);
    const target = await resolveEvolutionTarget(input.targetKind, input.targetId, rc);
    if (target.next_action === "inspect_loop") return target.evidence;
    const capabilities = await client.getEventEvolutionCapabilities();
    if (!capabilities.durable_loop_enabled) {
      throw new Error(`DURABLE_LOOP_UNAVAILABLE: ${capabilities.durable_loop_reason ?? "automatic loop is not enabled"}`);
    }
    if (target.next_action !== "start_loop" || !target.start_input?.seedStrategyId) {
      throw new Error(`EVOLUTION_TARGET_BLOCKED: ${target.blockers.join("; ") || target.next_action}`);
    }
    const config = eventCampaignConfigSchema.parse(target.start_input.config);
    const snapshot = getRequestContextValue<EvolutionLLMSnapshot>({ requestContext: rc }, USER_LLM_SNAPSHOT_KEY);
    const operationId = getRequestContextValue<string>({ requestContext: rc }, APPROVAL_OPERATION_ID_KEY);
    const authSub = rc?.get?.(AUTH_SUB_KEY);
    if (!snapshot || !operationId || typeof authSub !== "string" || !authSub) {
      throw new Error("owner-bound loop authorization context is missing");
    }
    const frozen = await createAutomaticEventSnapshot(config, rc);
    const baseline = buildEvolutionStartRequest({
      budget: input.budget, seedStrategyId: target.start_input.seedStrategyId,
      config: target.start_input.config, llmSnapshot: snapshot,
    });
    const campaign = buildEventCampaignRequest({
      eventSnapshotId: frozen.snapshotId, targetKind: input.targetKind, targetId: input.targetId,
      config: { ...config, asset_id: frozen.asset.asset_id, event_asset_code: frozen.asset.event_asset_code },
      llmSnapshot: snapshot,
    });
    const request = {
      baseline, campaign,
      max_cost_usd: input.maxCostUsd ?? Math.ceil(((input.budget ?? 4) + 13) * snapshot.pricing.estimated_max_usd_per_candidate * 1e6) / 1e6,
    };
    const credentialGrant = await mintEvolutionCredentialGrant({
      authSub, operationId, snapshot, purpose: "evolution_loop_start",
      requestDigest: evolutionLoopRequestDigest(request),
    });
    return await client.startEvolutionLoop({ request, idempotencyKey: operationId, credentialGrant });
  },
});
