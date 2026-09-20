import type { EvolutionLoopStatus } from "./types";

/** Return a stage only when its meaning is known; failures never imply graduation. */
export function loopStage(status: EvolutionLoopStatus): number | null {
  switch (status) {
    case "target_resolved": return 0;
    case "baseline_ready":
    case "campaign_running": return 1;
    case "candidate_locked":
    case "waiting_forward": return 2;
    case "holdout_running": return 3;
    case "adoption_ready": return 4;
    default: return null;
  }
}

/** Use low-frequency reads while research runs; stop polling terminal results. */
export function loopRefreshInterval(status?: EvolutionLoopStatus): number {
  if (!status) return 15_000;
  if (status === "waiting_forward") return 60_000;
  return loopStage(status) !== null && status !== "adoption_ready" ? 15_000 : 0;
}
