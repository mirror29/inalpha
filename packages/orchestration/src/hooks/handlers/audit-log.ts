/**
 * ``audit-log`` —— PostToolUse 通用审计 handler 样本。
 *
 * D-8 阶段先用 ``console.log`` JSON 行（stdout 收集）；持久化版本（写
 * ``audit/YYYY-MM-DD.jsonl``）等 telemetry 落地（ADR-0015）一起做。
 *
 * 审计规则：
 *
 * - 非阻塞（``blocking: false``）—— audit 失败不影响业务
 * - 默认 matcher：``"paper.* | live.* | factor.* | research.*"``（业务 tool）
 * - 输出敏感字段裁剪（``apiKey`` / ``secret`` / ``token`` 等做 mask）
 */
import type { HookHandler, HookRegistration } from "../types.js";

const SENSITIVE_KEYS = new Set([
  "apikey",
  "api_key",
  "secret",
  "token",
  "password",
  "passwd",
  "approvaltoken",
  "approval_token",
]);

function maskSensitive(value: unknown): unknown {
  if (value === null || typeof value !== "object") return value;
  if (Array.isArray(value)) return value.map(maskSensitive);

  const out: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(value)) {
    if (SENSITIVE_KEYS.has(k.toLowerCase())) {
      out[k] = "[REDACTED]";
    } else {
      out[k] = maskSensitive(v);
    }
  }
  return out;
}

/**
 * 创建 audit-log handler。可注入自定义 sink（默认 console.log）便于测试。
 */
export function createAuditLogHandler(
  sink: (record: Record<string, unknown>) => void = (r) => {
    // eslint-disable-next-line no-console
    console.log(JSON.stringify(r));
  },
): HookHandler {
  return async (ctx) => {
    const record = {
      event: ctx.event,
      tool: ctx.toolName,
      sessionId: ctx.sessionId,
      isError: ctx.isError ?? false,
      input: maskSensitive(ctx.toolInput),
      output: maskSensitive(ctx.toolOutput),
      ts: new Date().toISOString(),
    };
    sink(record);
    // 审计是纯副作用，不影响决策
  };
}

/**
 * 默认审计 hook 注册项（仅 PostToolUse；失败链一般还要单独的告警 handler）。
 */
export function defaultAuditRegistration(
  sink?: (record: Record<string, unknown>) => void,
): HookRegistration {
  return {
    id: "audit-log",
    event: "PostToolUse",
    matcher: "paper.* | live.* | factor.* | research.* | data.backfill_bars",
    handler: createAuditLogHandler(sink),
    blocking: false,
  };
}
