/**
 * ``strategy-code-audit`` —— D-9 · PreToolUse hook，限 LLM 写策略源码的尺寸 / 防注入。
 *
 * 真正的 Python 安全审计在 paper service 里跑（``strategy_authoring/ast_audit.py``
 * 三道沙盒）；本 TS hook 只做 **outer perimeter**：
 *
 * - 限源码长度（默认 20KB），超长直接 deny 不打 service（省一次 HTTP + Pydantic 反序列）
 * - 简单 prompt injection 字符串检测（"ignore previous instructions" 等）—— LLM 写策略
 *   不应该出现这类字串；出现 = 上游被注入或 LLM 跑偏，直接 deny
 *
 * Matcher 锁 ``paper.author_strategy``，不会误伤其它 tool。
 *
 * 设计取舍：不在这里做 AST 解析——重复 paper service 的工作，且 TS 没好用的 Python AST
 * 解析器。让 service 做真审计，hook 做廉价拦截。
 */
import type { HookHandler, HookRegistration } from "../types.js";

const DEFAULT_MAX_CODE_BYTES = 20_480;

// 简易 prompt-injection 关键词（LLM 写 Python 策略时正常不会出现）
const INJECTION_PATTERNS: readonly RegExp[] = [
  /ignore\s+(all\s+)?previous\s+instructions/i,
  /disregard\s+(all\s+)?(prior|above)\s+instructions/i,
  /you\s+are\s+now\s+a\s+different/i,
  // 经典 sandbox escape 字面量（即使 ast_audit 会拦，提前 deny 省一次 HTTP）
  /__class__\s*\.\s*__bases__/,
  /__subclasses__\s*\(/,
];

export type StrategyCodeAuditOptions = {
  /** 最大允许源码字节数（utf-8 编码），默认 20480。 */
  maxBytes?: number;
};

export function createStrategyCodeAuditHandler(
  opts: StrategyCodeAuditOptions = {},
): HookHandler {
  const maxBytes = opts.maxBytes ?? DEFAULT_MAX_CODE_BYTES;
  return (ctx) => {
    const input = ctx.toolInput as Record<string, unknown> | undefined;
    const codeRaw = input?.code;
    if (typeof codeRaw !== "string") return;

    // 1. 长度（按 utf-8 字节算，避免多字节字符过长仍放行）
    const byteLen = new TextEncoder().encode(codeRaw).length;
    if (byteLen > maxBytes) {
      return {
        permissionOverride: "deny",
        message:
          `STRATEGY_CODE_TOO_LARGE: ${byteLen} bytes > limit ${maxBytes}. ` +
          "把策略写紧凑一些；MVP 不需要 docstring / 过多注释。",
      };
    }

    // 2. 注入关键词
    for (const re of INJECTION_PATTERNS) {
      if (re.test(codeRaw)) {
        return {
          permissionOverride: "deny",
          message:
            `STRATEGY_CODE_INJECTION_BLOCKED: source contains pattern ${re.source!} ` +
            "which is not normal Python. Rewrite without it.",
        };
      }
    }
    return;
  };
}

export function defaultStrategyCodeAuditRegistration(
  opts: StrategyCodeAuditOptions = {},
): HookRegistration {
  return {
    id: "strategy-code-audit",
    event: "PreToolUse",
    matcher: "paper.author_strategy",
    handler: createStrategyCodeAuditHandler(opts),
    blocking: true,
  };
}
