/**
 * ``HookRunner`` —— 编排层 hooks 注册表 + 调度器。
 *
 * 用法：
 *
 * ```ts
 * const runner = new HookRunner();
 * runner.register({
 *   id: "audit-log",
 *   event: "PostToolUse",
 *   matcher: "paper.* | live.*",
 *   handler: async (ctx) => { auditLog(ctx); },
 *   blocking: false,
 * });
 *
 * const decision = await runner.run("PreToolUse", { toolName: "paper.run_backtest", toolInput: {...} });
 * ```
 *
 * 合并多个命中 hook 的决策（ADR-0010 §关键约束）：
 *
 * - **任意一个 hook 返回 ``deny``** → 整体 deny（最严格的胜出）
 * - 否则任意 ``ask`` → 整体 ask
 * - 否则任意 ``allow`` → 整体 allow
 * - ``updatedInput`` 后命中的覆盖先命中的（执行顺序 = 注册顺序）
 * - ``message`` 用 ``\n`` 拼接
 * - ``forceError`` / ``continue=false`` 任一为 true 即生效
 * - ``additionalContext`` 用 ``\n`` 拼接
 */
import { toolMatches } from "./matcher.js";
import type { HookContext, HookEvent, HookHandler, HookRegistration, MergedDecision } from "./types.js";

const DEFAULT_TIMEOUT_MS = 5_000;

class HookTimeoutError extends Error {
  constructor(id: string, timeoutMs: number) {
    super(`hook ${id} timed out after ${timeoutMs}ms`);
    this.name = "HookTimeoutError";
  }
}

/** 用 Promise.race 给单个 handler 套超时；超时按"取消"处理（保守）。 */
async function runWithTimeout(
  id: string,
  handler: HookHandler,
  ctx: HookContext,
  timeoutMs: number,
): Promise<ReturnType<HookHandler>> {
  let timer: ReturnType<typeof setTimeout> | undefined;
  const timeoutP = new Promise<never>((_, reject) => {
    timer = setTimeout(() => reject(new HookTimeoutError(id, timeoutMs)), timeoutMs);
  });
  try {
    return await Promise.race([Promise.resolve(handler(ctx)), timeoutP]);
  } finally {
    if (timer) clearTimeout(timer);
  }
}

export class HookRunner {
  private hooks: HookRegistration[] = [];

  /** 注册一个 hook。同 id 后注册覆盖前注册，方便测试 / 热替换。 */
  register(h: HookRegistration): void {
    this.hooks = this.hooks.filter((x) => x.id !== h.id);
    this.hooks.push(h);
  }

  /** 移除 hook。 */
  unregister(id: string): void {
    this.hooks = this.hooks.filter((x) => x.id !== id);
  }

  /** 已注册的 hook 列表（debug 用）。 */
  list(): HookRegistration[] {
    return [...this.hooks];
  }

  /** 跑某个 event 对应的所有命中 hook，返回合并决策。 */
  async run(event: HookEvent, ctx: Omit<HookContext, "event">): Promise<MergedDecision> {
    const merged: MergedDecision = { appliedHookIds: [] };
    const fullCtx: HookContext = { event, ...ctx };

    const matched = this.hooks.filter(
      (h) => h.event === event && toolMatches(h.matcher, ctx.toolName),
    );

    for (const h of matched) {
      const blocking = h.blocking !== false; // 默认 true
      const timeoutMs = h.timeoutMs ?? DEFAULT_TIMEOUT_MS;
      let decision;
      try {
        decision = await runWithTimeout(h.id, h.handler, fullCtx, timeoutMs);
      } catch (err) {
        if (blocking) {
          // blocking 失败 → 视作 deny + message 写理由
          merged.permissionOverride = "deny";
          merged.message = appendMessage(merged.message, `hook ${h.id} failed: ${formatErr(err)}`);
          merged.appliedHookIds.push(h.id);
          // blocking 失败立刻断链：后面 hook 不再跑（claw-code 实证语义）
          return merged;
        }
        // non-blocking 失败 → 只记 message
        merged.message = appendMessage(merged.message, `hook ${h.id} warn: ${formatErr(err)}`);
        continue;
      }

      if (!decision) continue;

      // 收紧规则：deny > ask > allow
      if (decision.permissionOverride) {
        merged.permissionOverride = mergePermission(
          merged.permissionOverride,
          decision.permissionOverride,
        );
      }
      if (decision.updatedInput !== undefined) {
        merged.updatedInput = decision.updatedInput;
      }
      if (decision.message) {
        merged.message = appendMessage(merged.message, decision.message);
      }
      if (decision.forceError) {
        merged.forceError = true;
      }
      if (decision.additionalContext) {
        merged.additionalContext = appendMessage(
          merged.additionalContext,
          decision.additionalContext,
        );
      }
      if (decision.continue === false) {
        merged.continue = false;
        if (decision.reason) {
          merged.reason = decision.reason;
        }
      }
      merged.appliedHookIds.push(h.id);

      // 若被 deny，后续 hook 没必要再跑（claw-code 行为：第一个 deny 终止链）
      if (merged.permissionOverride === "deny") {
        return merged;
      }
    }

    return merged;
  }
}

function mergePermission(
  current: "allow" | "deny" | "ask" | undefined,
  next: "allow" | "deny" | "ask",
): "allow" | "deny" | "ask" {
  const order = { deny: 3, ask: 2, allow: 1 } as const;
  if (!current) return next;
  return order[next] > order[current] ? next : current;
}

function appendMessage(prev: string | undefined, add: string): string {
  return prev ? `${prev}\n${add}` : add;
}

function formatErr(e: unknown): string {
  if (e instanceof Error) return e.message;
  return String(e);
}
