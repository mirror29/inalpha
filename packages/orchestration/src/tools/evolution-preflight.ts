import { loopInputSchema } from "./evolution-loop.js";
import { prepareExperiment } from "../evolution/preparation.js";
import { z } from "zod";
import { resolveEvolutionSeedContext } from "./evolution-target.js";
import { HttpClientError } from "../clients/http.js";
import { buildEvolutionStartRequest, evolutionRequestDigest } from "../clients/evolver.js";
import {
  getRequestContextValue,
  USER_LLM_SNAPSHOT_KEY,
  type EvolutionLLMSnapshot,
} from "../mastra/llm/evolution-snapshot.js";
import { evolutionConfigSchema, getEvolverClient, type ToolRequestContext } from "./evolver-shared.js";

const inputSchema = z.object({
  budget: z.number().int().min(1).max(20).default(4),
  seedStrategyId: z.string().min(1).max(128).default("sma_cross_v1"),
  retryOfRunId: z.string().uuid().optional(),
  config: evolutionConfigSchema.optional(),
});

/** Enforce deterministic preparation before the middleware creates a cost approval. */
export async function evolutionApprovalPreflight(toolName: string, input: unknown, ctx: unknown) {
  if (toolName === "evolver.start_evolution_loop") {
    const raw = loopInputSchema.parse(input);
    const rc = (ctx as { requestContext?: ToolRequestContext } | undefined)?.requestContext;
    const snapshot = getRequestContextValue<EvolutionLLMSnapshot>(ctx, USER_LLM_SNAPSHOT_KEY);
    if (!rc || !snapshot) throw new Error("EVOLUTION_MODEL_CONFIG_REQUIRED");
    const prepared = await prepareExperiment({ targetKind: raw.targetKind, targetId: raw.targetId,
      ...(raw.experiment ? { window: { from_ts: raw.experiment.from_ts, as_of: raw.experiment.as_of } } : {}),
    }, rc, raw.experiment?.eventSnapshotId);
    if (prepared.status === "existing_experiment" && raw.experiment) return { input: raw };
    if (prepared.status !== "necessary_inputs_present" || !("config" in prepared)) {
      throw new Error("E2_INPUT_COVERAGE_INSUFFICIENT: inspect preparation; no generation started");
    }
    const data = prepared as Record<string, unknown>;
    const config = data.config as { from_ts: string; as_of: string };
    const experiment = { config, from_ts: config.from_ts, as_of: config.as_of,
      eventSnapshotId: data.event_snapshot_id, seed_source_hash: data.seed_source_hash,
      dataset_content_sha256: data.dataset_content_sha256 };
    const maxCostUsd = raw.maxCostUsd ?? Math.ceil((raw.budget + 13) * snapshot.pricing.estimated_max_usd_per_candidate * 1e6) / 1e6;
    if (maxCostUsd < (raw.budget + 10) * snapshot.pricing.estimated_max_usd_per_candidate) throw new Error("LOOP_BUDGET_INSUFFICIENT");
    return { input: loopInputSchema.parse({ ...raw, experiment, maxCostUsd }),
      summary: { ...data, estimated_max_cost_usd: maxCostUsd, cost_scope: "evolution_generation_only" } };
  }
  if (toolName !== "evolver.run_evolution") return undefined;
  const snapshot = getRequestContextValue<EvolutionLLMSnapshot>(ctx, USER_LLM_SNAPSHOT_KEY);
  if (!snapshot) throw new Error("EVOLUTION_MODEL_CONFIG_REQUIRED: choose a model before preparation");
  const requestContext = (ctx as { requestContext?: ToolRequestContext } | undefined)?.requestContext;
  const client = await getEvolverClient(requestContext);
  try {
    const raw = inputSchema.parse(input);
    const seedContext = raw.retryOfRunId ? null : await resolveEvolutionSeedContext(raw.seedStrategyId, requestContext);
    const config = raw.config ?? seedContext?.config;
    if (!config) throw new Error("EVOLUTION_SEED_CONTEXT_REQUIRED: an explicit frozen configuration is required for built-in seeds and retries");
    const parsed = { ...raw, seedStrategyId: seedContext?.reference ?? raw.seedStrategyId, config: evolutionConfigSchema.parse(config) };
    const summary = await client.preflightRun(buildEvolutionStartRequest({
      ...parsed, llmSnapshot: snapshot,
    }));
    const preparation = z.object({
      seed_source_hash: z.string().regex(/^[0-9a-f]{64}$/),
      dataset_content_sha256: z.string().regex(/^[0-9a-f]{64}$/),
    }).parse({
      seed_source_hash: summary.seed_source_hash,
      dataset_content_sha256: summary.dataset_manifest.content_sha256,
    });
    const request = buildEvolutionStartRequest({ ...parsed, preparation, llmSnapshot: snapshot });
    if (summary.request_digest !== evolutionRequestDigest(request)) {
      throw new Error("EVOLUTION_PREPARATION_INVALID: preparation does not match the requested input");
    }
    return { input: { ...parsed, preparation }, summary: { ...summary, seed_context: seedContext?.evidence ?? null } };
  } catch (error) {
    if (!(error instanceof HttpClientError)) throw error;
    const missing = error.details.missing_count;
    const gap = typeof missing === "number" ? ` (${missing} missing closed bars)` : "";
    throw new Error(
      `${error.code}${gap}: evolution preparation failed; correct the owned seed or ` +
      "market data before requesting cost approval. No model generation was started.",
    );
  }
}
