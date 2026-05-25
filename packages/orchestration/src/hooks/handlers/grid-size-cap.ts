/**
 * ``grid-size-cap`` —— PreToolUse 上限护栏（ADR-0025 §D4）。
 *
 * **何时介入**：``swarm.run_backtest_grid`` 入参 ``strategies.length × symbols.length > MAX``
 * 时 deny。让 LLM 拆开多次调，避免 paper-service ProcessPool 被一次性塞爆。
 *
 * **为什么 hook 层做**：服务端 schema 也校验 max(5) / max(8)（GridInputSchema），但
 *
 * 1. hook 层 deny 给的反馈消息可以更具体（含 "拆开调" 引导）
 * 2. workflow 进入 expand step 前就拒，省一轮 mastra runtime 开销
 * 3. 业务策略调整时（比如临时收紧到 10）改 hook 即可，不动 schema
 *
 * 双层防御是预期的；schema 是兜底底线。
 */
import type { HookHandler, HookRegistration } from "../types.js";

export const DEFAULT_GRID_MAX = 20;

/** swarm.run_backtest_grid 输入形状（仅取需要的两个数组）。 */
type GridInputShape = {
  strategies?: unknown;
  symbols?: unknown;
};

function getArrayLen(value: unknown): number | null {
  return Array.isArray(value) ? value.length : null;
}

export function createGridSizeCapHandler(opts?: { max?: number }): HookHandler {
  const max = opts?.max ?? DEFAULT_GRID_MAX;
  return (ctx) => {
    const input = (ctx.toolInput ?? {}) as GridInputShape;
    const s = getArrayLen(input.strategies);
    const sym = getArrayLen(input.symbols);

    // 缺字段不在这层拦——交 zod / schema 校验报更明确的错
    if (s === null || sym === null) return;

    const total = s * sym;
    if (total > max) {
      return {
        permissionOverride: "deny",
        message:
          `grid 上限 ${max}，当前 ${total}（${s} strategies × ${sym} symbols）。` +
          `请拆成 ≤ ${max} job 的多次调用，或减少其中一边。`,
      };
    }
  };
}

/** 默认 grid-size-cap 注册项。挂在 PreToolUse + swarm.run_backtest_grid matcher。 */
export function defaultGridSizeCapRegistration(opts?: { max?: number }): HookRegistration {
  return {
    id: "grid-size-cap",
    event: "PreToolUse",
    matcher: "swarm.run_backtest_grid",
    handler: createGridSizeCapHandler(opts),
    blocking: true,
  };
}
