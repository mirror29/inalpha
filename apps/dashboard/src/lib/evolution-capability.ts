import "server-only";

import { backendFetch } from "./backend";

/** E1 Evolver connectivity switch; E2 mutations use the backend capability below. */
export function isEvolutionEnabled(): boolean {
  return process.env.EVOLVER_ENABLED?.toLowerCase() !== "false";
}

export type EventEvolutionCapability = {
  event_evolution_enabled: boolean;
  reason: string | null;
  max_generations: 5;
  hypotheses_per_generation: 8;
  implementations_per_hypothesis: 3;
  candidate_concurrency: number;
  supported_timeframes: Array<"15m" | "1h" | "4h">;
  automatic_stage_approval: true;
  automatic_promotion: false;
  runner_eligible: false;
};

/** Read the Evolver-owned E2 gate; callers must not infer it from Dashboard env. */
export async function getEventEvolutionCapability(): Promise<EventEvolutionCapability> {
  return await backendFetch<EventEvolutionCapability>("evolver", "/api/v1/capabilities", {
    timeoutMs: 5_000,
  });
}
