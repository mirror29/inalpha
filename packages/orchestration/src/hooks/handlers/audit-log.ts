/**
 * ``audit-log`` —— PostToolUse 通用审计 handler 样本。
 *
 * D-8 阶段先用 ``console.log`` JSON 行（stdout 收集）；持久化版本（写
 * ``audit/YYYY-MM-DD.jsonl``）等 telemetry 落地（ADR-0015）一起做。
 *
 * 审计规则：
 *
 * - 非阻塞（``blocking: false``）—— audit 失败不影响业务
 * - 默认 matcher：``"paper.* | live.* | factor.* | research.* | data.backfill_bars | skill.*"``
 *   （业务 tool + skill 触发率统计；纯读 data.get_bars / web.* 不进，避免噪音）
 * - 敏感字段脱敏 —— 凭据（apiKey / token / secret / password / approval_token）+
 *   PII（email / phone / address / wallet / ssn / credit card 等）一起 mask
 * - 字段名匹配**忽略大小写 + 下划线**（``api_key`` / ``apiKey`` / ``API_KEY`` 一视同仁）
 */
import { maskSensitive, normalizeKey } from "../../redact.js";
import type { HookHandler, HookRegistration } from "../types.js";

export type AuditLogOptions = {
  /** 自定义 sink（默认 console.log JSON 行）。 */
  sink?: (record: Record<string, unknown>) => void;
  /**
   * 额外脱敏字段名（在默认集合之外加）。同样会按 normalize 处理：
   * ``["walletId", "user_address"]`` 都会变成 ``walletid`` / ``useraddress``。
   */
  extraSensitiveKeys?: readonly string[];
};

/**
 * 创建 audit-log handler。可注入自定义 sink（默认 console.log）便于测试。
 *
 * 兼容旧签名 ``createAuditLogHandler(sink)`` 直接传 sink 函数。
 */
export function createAuditLogHandler(
  sinkOrOpts?: ((record: Record<string, unknown>) => void) | AuditLogOptions,
): HookHandler {
  const opts: AuditLogOptions =
    typeof sinkOrOpts === "function" ? { sink: sinkOrOpts } : (sinkOrOpts ?? {});
  const sink =
    opts.sink ??
    ((r: Record<string, unknown>) => {
      console.log(JSON.stringify(r));
    });
  const extra = new Set<string>((opts.extraSensitiveKeys ?? []).map((k) => normalizeKey(k)));

  return async (ctx) => {
    const record = {
      event: ctx.event,
      tool: ctx.toolName,
      sessionId: ctx.sessionId,
      isError: ctx.isError ?? false,
      input: maskSensitive(ctx.toolInput, extra),
      output: maskSensitive(ctx.toolOutput, extra),
      ts: new Date().toISOString(),
    };
    sink(record);
    // 审计是纯副作用，不影响决策
  };
}

/**
 * 默认审计 hook 注册项（仅 PostToolUse；失败链一般还要单独的告警 handler）。
 *
 * 接受 ``sink`` 函数或完整的 ``AuditLogOptions``（含 ``extraSensitiveKeys``）。
 */
export function defaultAuditRegistration(
  sinkOrOpts?: ((record: Record<string, unknown>) => void) | AuditLogOptions,
): HookRegistration {
  return {
    id: "audit-log",
    event: "PostToolUse",
    // skill.*：ADR-0046 Open Question 1——统计 skill.read 触发率（DeepSeek 对
    // "清单 + 按需读"的遵从度），决定要不要把高频 skill 摘要升级进常驻 prompt 段。
    matcher: "paper.* | live.* | factor.* | research.* | data.backfill_bars | skill.*",
    handler: createAuditLogHandler(sinkOrOpts),
    blocking: false,
  };
}
