/**
 * ``evolution-on-promote`` —— PostToolUse hook。
 *
 * 在 ``paper.promote_candidate`` 执行成功后将候选源码作为种子自动触发
 * 一轮演化（E2），实现"promote → 自动演化下一代"的链式闭环。
 *
 * 设计要点：
 *
 * - 非阻塞（``blocking: false``）—— promote 本身不因演化失败而回滚
 * - 异步 fire-and-forget —— hook handler 里 startRun，不 await 结果
 *   （演化耗时数分钟，不应拖长 promote 的响应时间）
 * - 每次 promote 触发 budget=2 的小规模探索（避免 budget 大 → LLM 费用高）
 * - 演化结果落 evolver 内存表，用户可通过 evolver.get_evolution 查看
 *
 * 坑：
 * - evolver 服务不可用时静默失败（只 log warn，不抛错到 promote 调用者）
 * - evolver 进程重启后内存表丢失 —— 演化结果只在 evolver 存活期间可查
 * - 若用户连续 promote 多个候选，每个都会触发一次演化，可能并行跑多个
 */
import { getSettings } from "../../config.js";
import { mintServiceToken } from "../../auth.js";
import { EvolverClient } from "../../clients/evolver.js";
import type { HookHandler, HookRegistration } from "../types.js";

export type EvolutionOnPromoteOptions = {
  /** 每次 promote 触发的演化 budget（默认 2） */
  budget?: number;
  /** 是否启用（默认 true）；测试时可关闭 */
  enabled?: boolean;
};

/**
 * 创建 promote → 演化 hook handler。
 *
 * 在 promote 成功后，以 promoted 候选的源码为种子跑一轮演化。
 */
export function createEvolutionOnPromoteHandler(
  opts: EvolutionOnPromoteOptions = {},
): HookHandler {
  const budget = opts.budget ?? 2;
  const enabled = opts.enabled ?? true;

  return async (ctx) => {
    if (!enabled) return;
    if (ctx.event !== "PostToolUse") return;

    // 只对成功的 promote 触发
    if (ctx.isError) return;
    const output = ctx.toolOutput as Record<string, unknown> | undefined;
    if (!output) return;

    // 从 promote 返回提取 candidate_id
    // paper.promote_candidate 返回 { id, status, ... } 或 { candidate_id, ... }
    const candidateId =
      (output.id as string | undefined) ??
      (output.candidate_id as string | undefined);
    if (!candidateId) {
      console.warn(
        "[evolution-on-promote] promote response missing candidate_id, skipping evolution",
      );
      return;
    }

    // fire-and-forget：不阻塞 promote 的返回
    triggerEvolution(candidateId, budget).catch((err) => {
      console.warn(
        `[evolution-on-promote] failed to trigger evolution for ${candidateId}:`,
        err,
      );
    });
  };
}

/**
 * 以 promoted 的候选为种子触发一次演化。
 * 异步执行，不阻塞 hook 调用者。
 */
async function triggerEvolution(
  candidateId: string,
  budget: number,
): Promise<void> {
  const settings = getSettings();
  const token = await mintServiceToken({
    sub: "service:orchestration",
    reason: "evolution-on-promote",
  });

  const client = new EvolverClient({
    baseUrl: settings.evolverServiceUrl,
    token,
    // 演化可能数分钟，用长超时
    timeoutMs: 600_000,
  });

  // 以 promoted 候选为种子，跑 budget 个变异
  // 传 seed_strategy_id = candidate:<uuid> 让 evolver 能 trace 血缘
  const result = await client.startRun(
    budget,
    `candidate:${candidateId}`,
    undefined,
  );

  console.info(
    `[evolution-on-promote] evolution started for ${candidateId}: ` +
      `run_id=${result.run_id}, budget=${budget}, status=${result.status}`,
  );
}

/**
 * 默认 promote → 演化 hook 注册项。
 */
export function defaultEvolutionOnPromoteRegistration(
  opts: EvolutionOnPromoteOptions = {},
): HookRegistration {
  return {
    id: "evolution-on-promote",
    event: "PostToolUse",
    // 只监听 paper.promote_candidate 的成功返回
    matcher: "paper.promote_candidate",
    handler: createEvolutionOnPromoteHandler(opts),
    // 非阻塞 —— promote 不因演化失败而回滚
    blocking: false,
  };
}