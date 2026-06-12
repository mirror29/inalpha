/**
 * ``redact`` —— 敏感字段脱敏（凭据 + PII），按**字段名**递归 mask。
 *
 * 任何把 tool 输入/输出持久化的路径都该过这里，保持各落库点处理对称：
 * - PostToolUse 审计日志（``hooks/handlers/audit-log.ts``）
 * - ask 审批历史（``permissions/repo.ts`` 的 ``insertPending``）
 *
 * 字段名匹配忽略大小写 + 下划线 / 短横线（``api_key`` / ``apiKey`` / ``API-KEY``
 * 一视同仁）。注意：只按字段名 mask，URL/字符串内嵌的凭据不在覆盖范围（那类
 * 应在产生处避免拼进明文）。
 */

/**
 * 默认脱敏键集合 —— 凭据 + 常见 PII。全部小写、去分隔符后存入；运行时同样
 * normalize 后比对。
 */
export const DEFAULT_SENSITIVE_KEYS: readonly string[] = [
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
export function normalizeKey(s: string): string {
  return s.replace(/[_\-]/g, "").toLowerCase();
}

/** 字段名是否落在默认或额外脱敏集合内（入参 ``extra`` 须已 normalize）。 */
function isSensitive(key: string, extra: Set<string>): boolean {
  const norm = normalizeKey(key);
  return DEFAULT_NORMALIZED.has(norm) || extra.has(norm);
}

/**
 * 递归 mask：命中敏感字段名 → ``[REDACTED]``；其余原样（对象/数组深入）。
 *
 * @param value 待脱敏的任意值（通常是 tool 输入/输出）。
 * @param extra 额外脱敏字段名集合（须已 normalize；默认空）。
 */
export function maskSensitive(
  value: unknown,
  extra: Set<string> = new Set(),
): unknown {
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
