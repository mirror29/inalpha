/**
 * ``tool-idempotency`` —— PreToolUse + PostToolUse 配对 hook，挡住 LLM 同 session
 * 内对**确定性 tool** 的重复调用（fix ADR-0025 follow-up：DeepSeek 在 Mastra agent
 * loop 里多次 retry 同 swarm.run_backtest_grid 调用的现象）。
 *
 * 行为：
 *
 * - **PostToolUse**：成功 + 非错误时，把 ``(sessionId, toolName, stableInputJSON)``
 *   作 key 存进 in-memory ``Map``，value = output + ts
 * - **PreToolUse**：命中 cache 且未过 TTL → 返 ``permissionOverride: "deny"`` +
 *   ``message`` 含"已经跑过这个 input，请复用上次结果"的明确提示（带 summary）
 *
 * 适用范围：**输入到输出确定性映射**的 tool（同 input 必同 output）——
 * - swarm.run_backtest_grid ✓ （回测对历史数据是确定性的）
 * - paper.run_backtest ✓
 * - paper.compose_strategy ✓
 *
 * **不**适用于：
 * - 时间敏感读（data.get_ticker / get_bars fresh=true）—— 拉的是 live 现价
 * - 写操作（trade.create_plan / approve / execute）—— 每次都是新事件
 *
 * 因此默认 matcher 限定到 swarm，避免误伤；调用方按需扩展 matcher。
 *
 * TTL 默认 60 秒（同 turn 通常 < 30s，给 1 分钟有冗余；过期清除避免内存涨）。
 */
import type { HookHandler, HookRegistration } from "../types.js";

type CacheEntry = {
  output: unknown;
  ts: number;
};

/** stable JSON stringify —— object keys 按字典序，避免 {a,b} vs {b,a} 不命中。 */
function stableStringify(value: unknown): string {
  if (value === null || typeof value !== "object") return JSON.stringify(value);
  if (Array.isArray(value)) return `[${value.map(stableStringify).join(",")}]`;
  const keys = Object.keys(value as Record<string, unknown>).sort();
  const obj = value as Record<string, unknown>;
  return `{${keys.map((k) => `${JSON.stringify(k)}:${stableStringify(obj[k])}`).join(",")}}`;
}

function buildKey(sessionId: string | undefined, toolName: string | undefined, input: unknown): string {
  // 没 sessionId 时回落 "global" —— 测试 / 手动调用场景仍能跑 idempotency
  const sid = sessionId ?? "global";
  return `${sid}|${toolName ?? "?"}|${stableStringify(input)}`;
}

/** 给 deny message 用：把输出截短到可读概要。 */
function summarizeOutput(output: unknown): string {
  try {
    const s = JSON.stringify(output);
    if (s.length <= 400) return s;
    return s.slice(0, 400) + "...(truncated)";
  } catch {
    return String(output);
  }
}

export type IdempotencyOptions = {
  /** 缓存 TTL（毫秒），默认 60_000ms = 1 分钟。 */
  ttlMs?: number;
  /** 自定义 cache 容器（测试 / 多实例隔离用）；缺省内部新建 Map。 */
  cache?: Map<string, CacheEntry>;
};

export type IdempotencyPair = {
  pre: HookHandler;
  post: HookHandler;
  /** 暴露内部 cache 给测试 / 监控用。 */
  cache: Map<string, CacheEntry>;
};

/** 创建一对 hook handler（pre + post），共享同一个 cache。 */
export function createToolIdempotencyHandlers(opts: IdempotencyOptions = {}): IdempotencyPair {
  const ttlMs = opts.ttlMs ?? 60_000;
  const cache = opts.cache ?? new Map<string, CacheEntry>();

  const pre: HookHandler = (ctx) => {
    const key = buildKey(ctx.sessionId, ctx.toolName, ctx.toolInput);
    const cached = cache.get(key);
    if (!cached) return;

    const age = Date.now() - cached.ts;
    if (age > ttlMs) {
      cache.delete(key);
      return;
    }

    // 命中：deny，把上次结果摘要给 LLM 当作"请用这个，别再调"信号
    return {
      permissionOverride: "deny",
      message:
        `IDEMPOTENT_DUP: identical ${ctx.toolName} call already executed ${Math.round(age / 1000)}s ago in this session. ` +
        `**Do NOT call this tool again with the same input** — reuse the previous result below and proceed to the next step.\n` +
        `previous_result: ${summarizeOutput(cached.output)}`,
    };
  };

  const post: HookHandler = (ctx) => {
    if (ctx.isError) return; // 不缓存失败
    const key = buildKey(ctx.sessionId, ctx.toolName, ctx.toolInput);
    cache.set(key, { output: ctx.toolOutput, ts: Date.now() });
  };

  return { pre, post, cache };
}

/**
 * 默认注册项（一对）：matcher 锁定 ``swarm.*``。
 *
 * 调用方想扩到其它确定性 tool（如 paper.run_backtest）时，自己写 matcher 拼接更广的
 * pattern，再用同一对 handler 注册。
 */
export function defaultIdempotencyRegistrations(opts: IdempotencyOptions = {}): {
  pre: HookRegistration;
  post: HookRegistration;
} {
  const { pre, post } = createToolIdempotencyHandlers(opts);
  return {
    pre: {
      id: "tool-idempotency-pre",
      event: "PreToolUse",
      matcher: "swarm.*",
      handler: pre,
      blocking: true,
    },
    post: {
      id: "tool-idempotency-post",
      event: "PostToolUse",
      matcher: "swarm.*",
      handler: post,
      blocking: false,
    },
  };
}
