import { z } from "zod";
import { HttpClientError } from "../clients/http.js";
import { buildEvolutionStartRequest } from "../clients/evolver.js";
import {
  getRequestContextValue,
  USER_LLM_SNAPSHOT_KEY,
  type EvolutionLLMSnapshot,
} from "../mastra/llm/evolution-snapshot.js";
import { evolutionConfigSchema, getEvolverClient, type ToolRequestContext } from "./evolver-shared.js";

const inputSchema = z.object({
  budget: z.number().int().min(1).max(20).default(4),
  seedStrategyId: z.string().min(1).max(128).default("sma_cross_v1"),
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
    return await client.preflightRun(buildEvolutionStartRequest({
      ...inputSchema.parse(input), llmSnapshot: snapshot,
    }));
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
