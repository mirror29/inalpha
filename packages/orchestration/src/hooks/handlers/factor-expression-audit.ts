/**
 * ``factor-expression-audit`` —— D-12 · PreToolUse hook，自定义因子表达式的廉价外围拦截。
 *
 * 真审计在 factor service（``expression.py`` 白名单 DSL：节点/算子/字面量/window
 * 全在解析期校验）；本 TS hook 只做 **outer perimeter**，挡明显违例省一次 HTTP：
 *
 * - 长度上限（2KB，与服务端 MAX_EXPRESSION_LENGTH 对齐）
 * - 负 lag 字面量（``Ref($x, -n)`` = 向未来看，lookahead 三层防线的第一层）
 * - 未来语义命名（future/ahead/tomorrow/next_/will_——表达式里出现 = 意图就歪了）
 * - prompt injection 字面量
 *
 * Matcher 锁 ``factor.evaluate_candidate``，不误伤其它 tool。
 */
import type { HookHandler, HookRegistration } from "../types.js";

const DEFAULT_MAX_EXPR_BYTES = 2_048;

const NEGATIVE_LAG = /\b(Ref|Delta)\s*\(\s*[^,)]*,\s*-\s*\d/;

const FUTURE_NAMING: readonly RegExp[] = [
  /future|ahead|tomorrow|next_|will_/i,
];

const INJECTION_PATTERNS: readonly RegExp[] = [
  /ignore\s+(all\s+)?previous\s+instructions/i,
  /__import__|__class__|eval\s*\(|exec\s*\(/,
];

export type FactorExpressionAuditOptions = {
  /** 最大表达式字节数（utf-8），默认 2048（与服务端上限一致）。 */
  maxBytes?: number;
};

export function createFactorExpressionAuditHandler(
  opts: FactorExpressionAuditOptions = {},
): HookHandler {
  const maxBytes = opts.maxBytes ?? DEFAULT_MAX_EXPR_BYTES;
  return (ctx) => {
    const input = ctx.toolInput as Record<string, unknown> | undefined;
    const expr = input?.expression;
    if (typeof expr !== "string") return;

    const byteLen = new TextEncoder().encode(expr).length;
    if (byteLen > maxBytes) {
      return {
        permissionOverride: "deny",
        message:
          `FACTOR_EXPRESSION_TOO_LARGE: ${byteLen} bytes > limit ${maxBytes}. ` +
          "因子表达式应是一行紧凑公式，不是程序。",
      };
    }

    if (NEGATIVE_LAG.test(expr)) {
      return {
        permissionOverride: "deny",
        message:
          "FACTOR_EXPRESSION_LOOKAHEAD: Ref/Delta 带负 lag = 向未来看（lookahead bias）。" +
          "lag 必须是正整数；想表达\"过去 n 根 bar\"就写正数。",
      };
    }

    for (const re of FUTURE_NAMING) {
      if (re.test(expr)) {
        return {
          permissionOverride: "deny",
          message:
            `FACTOR_EXPRESSION_FUTURE_NAMING: 表达式含未来语义命名（${re.source}）。` +
            "因子只能由历史 bar 构成；前瞻收益由服务端统一计算，不要试图在表达式里造。",
        };
      }
    }

    for (const re of INJECTION_PATTERNS) {
      if (re.test(expr)) {
        return {
          permissionOverride: "deny",
          message:
            `FACTOR_EXPRESSION_INJECTION_BLOCKED: contains pattern ${re.source} ` +
            "which is not part of the factor DSL. Rewrite without it.",
        };
      }
    }
    return;
  };
}

export function defaultFactorExpressionAuditRegistration(
  opts: FactorExpressionAuditOptions = {},
): HookRegistration {
  return {
    id: "factor-expression-audit",
    event: "PreToolUse",
    matcher: "factor.evaluate_candidate",
    handler: createFactorExpressionAuditHandler(opts),
    blocking: true,
  };
}
