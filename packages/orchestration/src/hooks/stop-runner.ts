/**
 * Stop hook 调度器 —— 包 ``HookRunner`` 加 ``max_force_continue`` 限流。
 *
 * ADR-0010 §Stop hook 关键约定 5：每个 turn 强制 continue 次数有上限（默认 3），
 * 防 hook 永远说"还没干完"让 LLM 卡死循环。
 *
 * 用法（在 Mastra agent.stream 的 ``onFinish`` / 类似 lifecycle 处接）：
 *
 * ```ts
 * const stopRunner = new StopHookRunner(hookRunner);
 * const decision = await stopRunner.maybeForceContinue({
 *   sessionId: ctx.threadId,
 *   metadata: { agent: "orchestrator" },
 * });
 * if (!decision.shouldContinue) return finalMessage;
 * // 否则注入 [system_notice] 消息让 LLM 再 turn 一次
 * ```
 *
 * Mastra runtime 接入：D-9+ 工程，等 mastra 暴露稳定的 stop event 钩子位再做。
 * 当前文件提供完整逻辑 + 单测，集成留接口注入。
 */
import type { HookRunner } from "./runner.js";
import type { HookContext, MergedDecision } from "./types.js";

const DEFAULT_MAX_FORCE_CONTINUE = 3;

export type StopDecision = {
  shouldContinue: boolean;
  reason: string | null;
  /** 累计 force-continue 次数（达到 max 后即使 hook 还说 continue 也放行结束）。 */
  forceCount: number;
  /** debug：哪些 hook 参与决策 */
  appliedHookIds: string[];
};

export class StopHookRunner {
  private counts = new Map<string, number>(); // sessionId → 累计强制次数

  constructor(
    private readonly hookRunner: HookRunner,
    private readonly maxForceContinue: number = DEFAULT_MAX_FORCE_CONTINUE,
  ) {}

  /** 跑 Stop hook，返合并后的决策（含 force-continue 限流）。 */
  async maybeForceContinue(
    ctx: Omit<HookContext, "event">,
  ): Promise<StopDecision> {
    const merged: MergedDecision = await this.hookRunner.run("Stop", ctx);

    if (merged.continue === false) {
      const key = ctx.sessionId ?? "__no_session__";
      const prior = this.counts.get(key) ?? 0;
      if (prior >= this.maxForceContinue) {
        // 达上限：不再强 continue，避免 LLM 永远 turn
        return {
          shouldContinue: false,
          reason: null,
          forceCount: prior,
          appliedHookIds: merged.appliedHookIds,
        };
      }
      const next = prior + 1;
      this.counts.set(key, next);
      return {
        shouldContinue: true,
        reason: merged.reason ?? merged.message ?? "stop hook requested continue",
        forceCount: next,
        appliedHookIds: merged.appliedHookIds,
      };
    }

    // 不强制 continue：清掉该 session 计数（成功 stop = 周期重置）
    if (ctx.sessionId) this.counts.delete(ctx.sessionId);
    return {
      shouldContinue: false,
      reason: null,
      forceCount: 0,
      appliedHookIds: merged.appliedHookIds,
    };
  }

  /** 测试 / 手动清除某 session 的计数。 */
  resetSession(sessionId: string): void {
    this.counts.delete(sessionId);
  }
}

/**
 * 生成 [system_notice] 前缀的 prompt，供 Mastra agent 接入时注入回 conversation。
 * 按 ADR-0010 §Stop hook 关键约定 6 加固定 prefix 与用户消息区分。
 */
export function formatStopNotice(reason: string): string {
  return `[system_notice] ${reason}`;
}
