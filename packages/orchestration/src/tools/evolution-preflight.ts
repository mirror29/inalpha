import { z } from "zod";
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
  config: evolutionConfigSchema,
});

/** Enforce deterministic preparation before the middleware creates a cost approval. */
export async function evolutionApprovalPreflight(toolName: string, input: unknown, ctx: unknown) {
  if (toolName !== "evolver.run_evolution") return undefined;
  const snapshot = getRequestContextValue<EvolutionLLMSnapshot>(ctx, USER_LLM_SNAPSHOT_KEY);
  if (!snapshot) throw new Error("EVOLUTION_MODEL_CONFIG_REQUIRED: choose a model before preparation");
  const requestContext = (ctx as { requestContext?: ToolRequestContext } | undefined)?.requestContext;
  const client = await getEvolverClient(requestContext);
  try {
    const parsed = inputSchema.parse(input);
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
    return { input: { ...parsed, preparation }, summary };
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
