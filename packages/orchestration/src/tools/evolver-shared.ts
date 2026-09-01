/** Evolver Mastra tools 的共享 schema 与客户端解析。 */
import { z } from "zod";
import { resolveRequestToken } from "../auth.js";
import {
  buildEvolutionStartRequest,
  buildEventCampaignRequest,
  eventCampaignRequestDigest,
  evolutionRequestDigest,
  EvolverClient,
  type EvolutionConfig,
  type EvolutionStartRequest,
  type EventCampaignConfigInput,
} from "../clients/evolver.js";
import { getSettings } from "../config.js";
import { AUTH_SUB_KEY } from "../hooks/with-hooks.js";
import {
  APPROVAL_OPERATION_ID_KEY,
  getRequestContextValue,
  USER_LLM_SNAPSHOT_KEY,
  type EvolutionLLMSnapshot,
} from "../mastra/llm/evolution-snapshot.js";
import { mintEvolutionCredentialGrant } from "../mastra/llm/evolution-credential-grant.js";

export type ToolRequestContext = { authToken?: string; get?: (key: string) => unknown };

export async function getEvolverClient(ctx?: ToolRequestContext): Promise<EvolverClient> {
  return new EvolverClient({
    baseUrl: getSettings().evolverServiceUrl,
    token: await resolveRequestToken(ctx),
    timeoutMs: 30_000,
  });
}

export async function getApprovedEvolutionRunContext(
  input: { budget?: number; seedStrategyId?: string; config: EvolutionConfig },
  ctx?: ToolRequestContext,
): Promise<{
  client: EvolverClient;
  operationId: string;
  credentialGrant: string;
  llmSnapshot: EvolutionLLMSnapshot;
  request: EvolutionStartRequest;
}> {
  const operationId = getRequestContextValue<string>(
    { requestContext: ctx },
    APPROVAL_OPERATION_ID_KEY,
  );
  const llmSnapshot = getRequestContextValue<EvolutionLLMSnapshot>(
    { requestContext: ctx },
    USER_LLM_SNAPSHOT_KEY,
  );
  const authSub = ctx?.get?.(AUTH_SUB_KEY);
  if (!operationId || !llmSnapshot || typeof authSub !== "string" || !authSub) {
    throw new Error("explicit evolution approval context is missing");
  }
  const request = buildEvolutionStartRequest({ ...input, llmSnapshot });
  const credentialGrant = await mintEvolutionCredentialGrant({
    authSub,
    operationId,
    requestDigest: evolutionRequestDigest(request),
    snapshot: llmSnapshot,
  });
  return {
    client: await getEvolverClient(ctx),
    operationId,
    credentialGrant,
    llmSnapshot,
    request,
  };
}

/** Build one approved campaign context; its internal five generations remain automatic. */
export async function getApprovedEventCampaignContext(
  input: {
    eventSnapshotId: string;
    sourceRunId?: string;
    config: EventCampaignConfigInput;
  },
  ctx?: ToolRequestContext,
) {
  const operationId = getRequestContextValue<string>(
    { requestContext: ctx },
    APPROVAL_OPERATION_ID_KEY,
  );
  const llmSnapshot = getRequestContextValue<EvolutionLLMSnapshot>(
    { requestContext: ctx },
    USER_LLM_SNAPSHOT_KEY,
  );
  const authSub = ctx?.get?.(AUTH_SUB_KEY);
  if (!operationId || !llmSnapshot || typeof authSub !== "string" || !authSub) {
    throw new Error("explicit event campaign approval context is missing");
  }
  const request = buildEventCampaignRequest({
    eventSnapshotId: input.eventSnapshotId,
    sourceRunId: input.sourceRunId,
    config: input.config,
    llmSnapshot,
  });
  const credentialGrant = await mintEvolutionCredentialGrant({
    authSub,
    operationId,
    purpose: "event_campaign",
    requestDigest: eventCampaignRequestDigest(request),
    snapshot: llmSnapshot,
  });
  return {
    client: await getEvolverClient(ctx),
    operationId,
    credentialGrant,
    request,
  };
}

export const evolutionConfigSchema = z.object({
  venue: z.string().min(1).describe("数据 venue；按标的市场选择，不预设市场"),
  symbol: z.string().min(1).describe("该 venue 使用的标的代码"),
  timeframe: z.enum([
    "1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h",
    "6h", "8h", "12h", "1d", "3d", "1wk", "1mo",
  ]),
  from_ts: z.string().datetime().describe("评估窗口起点，ISO-8601 UTC"),
  as_of: z.string().datetime().describe("真实当前评估时点，ISO-8601 UTC"),
  initial_cash: z.number().min(100).default(10_000),
  fee_rate: z.number().min(0).max(0.1).default(0.001),
  validation_split: z.number().min(0).max(0.5).default(0.3),
});

export const eventCampaignConfigSchema = z.object({
  venue: z.string().min(1),
  symbol: z.string().min(1),
  timeframe: z.enum(["15m", "1h", "4h"]),
  from_ts: z.string().datetime(),
  as_of: z.string().datetime(),
  initial_cash: z.number().min(100).default(10_000),
  fee_rate: z.number().min(0).max(0.1).default(0.001),
  trading_mode: z.enum(["spot", "perp"]).default("perp"),
  leverage: z.number().int().min(1).max(20).default(1),
  random_seed: z.number().int().min(0).max(2 ** 31 - 1).default(0),
});
