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
import type { HookHandler, HookRegistration } from "../types.js";

/**
 * 默认脱敏键集合 —— 凭据 + 常见 PII。
 *
 * 全部小写、去下划线后存入；运行时同样 normalize 后比对。这样
 * ``apiKey`` / ``api_key`` / ``API_KEY`` / ``API-KEY`` 都命中同一条规则。
 */
const DEFAULT_SENSITIVE_KEYS: readonly string[] = [
  // 凭据
  "apikey",
  "secret",
  "token",
  "password",
  "passwd",
  "passphrase",
  "approvaltoken",
  "privatekey",
  "private_key",
  "accesskey",
  "refreshtoken",
  "sessionsecret",
  // 个人 PII
  "email",
  "phone",
  "phonenumber",
  "mobile",
  "address",
  "homeaddress",
  "walletaddress",
  "wallet",
  "ssn",
  "socialsecurity",
  "passport",
  "idnumber",
  // 支付
  "creditcard",
  "cardnumber",
  "cvv",
  "cvc",
  "iban",
  "swift",
];

const DEFAULT_NORMALIZED = new Set<string>(
  DEFAULT_SENSITIVE_KEYS.map((k) => normalizeKey(k)),
);

/** 大小写 + 下划线 / 短横线无关的字段名 normalize（``API_KEY`` → ``apikey``）。 */
function normalizeKey(s: string): string {
  return s.replace(/[_\-]/g, "").toLowerCase();
}

/**
 * 检查字段名是否被 normalized 集合覆盖。
 */
function isSensitive(key: string, extra: Set<string>): boolean {
  const norm = normalizeKey(key);
  return DEFAULT_NORMALIZED.has(norm) || extra.has(norm);
}

function maskSensitive(value: unknown, extra: Set<string>): unknown {
  if (value === null || typeof value !== "object") return value;
  if (Array.isArray(value)) return value.map((v) => maskSensitive(v, extra));

  const out: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(value)) {
    if (isSensitive(k, extra)) {
      out[k] = "[REDACTED]";
    } else {
      out[k] = maskSensitive(v, extra);
    }
  }
  return out;
}

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
