import { backendFetch } from "./backend";
import type {
  EvolutionLoop,
  EvolutionLineage,
  StrategyCandidateRecord,
  StrategyRunRecord,
} from "./types";

const UUID =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

/** Resolve a readable target through the same owner-scoped backend identity as the loop. */
export async function withDisplayTarget(
  loop: EvolutionLoop,
): Promise<EvolutionLoop> {
  if (!UUID.test(loop.target_id ?? "")) return loop;
  try {
    let candidateId: string;
    let href: string;
    if (loop.target_kind === "strategy_candidate") {
      candidateId = loop.target_id;
      href = `/lab/${candidateId}`;
    } else if (loop.target_kind === "paper_runner") {
      const runner = await backendFetch<StrategyRunRecord>(
        "paper",
        `/strategy_runs/${loop.target_id}`,
        { timeoutMs: 2000 },
      );
      if (!loop.owner_account_id || runner.account_id !== loop.owner_account_id)
        return { ...loop, display_target: null };
      candidateId = runner.candidate_id;
      href = `/runners/${loop.target_id}`;
    } else if (loop.target_kind === "e1_candidate") {
      return {
        ...loop,
        display_target: {
          description: "",
          href: `/evolution/candidates/${loop.target_id}`,
        },
      };
    } else if (loop.target_kind === "backtest_run") {
      return {
        ...loop,
        display_target: {
          description: "",
          href: `/backtests/${loop.target_id}`,
        },
      };
    } else return loop;
    if (!UUID.test(candidateId)) return { ...loop, display_target: null };
    const candidate = await backendFetch<StrategyCandidateRecord>(
      "paper",
      `/strategy_candidates/${candidateId}`,
      { timeoutMs: 2000 },
    );
    if (
      !loop.owner_account_id ||
      candidate.owner_account_id !== loop.owner_account_id
    ) {
      return { ...loop, display_target: null };
    }
    return {
      ...loop,
      display_target: {
        description: candidate.description?.trim().slice(0, 240) ?? "",
        href,
      },
    };
  } catch {
    return { ...loop, display_target: null };
  }
}

/** Bound target lookup concurrency; missing targets never hide readable research records. */
export async function withDisplayTargets(
  loops: EvolutionLoop[],
): Promise<EvolutionLoop[]> {
  const result: EvolutionLoop[] = [];
  const deadline = Date.now() + 4000;
  for (let index = 0; index < loops.length; index += 4) {
    if (Date.now() >= deadline) {
      result.push(
        ...loops
          .slice(index)
          .map((loop) => ({ ...loop, display_target: null })),
      );
      break;
    }
    result.push(
      ...(await Promise.all(
        loops.slice(index, index + 4).map(withDisplayTarget),
      )),
    );
  }
  return result;
}

/** Enriches only a verified source reference; a missing source does not hide lineage. */
export async function withLineageOrigin(
  lineage: EvolutionLineage | null | undefined,
): Promise<EvolutionLineage | null> {
  if (!lineage) return null;
  const reference = lineage.source_reference ?? "";
  const [prefix, id, extra] = reference.split(":");
  if (
    extra !== undefined ||
    !UUID.test(id ?? "") ||
    !["candidate", "evolution_candidate"].includes(prefix)
  ) {
    return { ...lineage, display_origin: null };
  }
  const target = await withDisplayTarget({
    owner_account_id: lineage.owner_account_id,
    target_kind: prefix === "candidate" ? "strategy_candidate" : "e1_candidate",
    target_id: id,
  } as EvolutionLoop);
  return { ...lineage, display_origin: target.display_target ?? null };
}
