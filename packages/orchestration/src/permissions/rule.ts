/**
 * Permission 规则字符串解析（ADR-0011 §matcher）。
 *
 * 把 ``"live.submit_order(notional>1000)"`` 拆成：
 *
 *   - toolPattern: ``"live.submit_order"``
 *   - predicate:   ``{ conditions: [{notional, >, 1000}] }``
 *
 * tool pattern 支持 ``"tool.name"`` / ``"tool.*"`` / ``"*"``（与 hooks/matcher 同语义）。
 */
import { parsePredicate } from "./predicate.js";
import type { Decision, ParsedRule } from "./types.js";

/** 拆出 toolPattern 与 predicate（如果有）。 */
export function parseRule(source: string, decision: Decision): ParsedRule {
  const trimmed = source.trim();
  const openParen = trimmed.indexOf("(");

  if (openParen === -1) {
    // 纯 tool pattern
    return { toolPattern: trimmed, predicate: null, decision, source };
  }

  if (!trimmed.endsWith(")")) {
    throw new Error(`permission rule ${JSON.stringify(source)}: missing closing ')'`);
  }

  const toolPattern = trimmed.slice(0, openParen).trim();
  const predSrc = trimmed.slice(openParen + 1, -1);
  const predicate = parsePredicate(predSrc);

  return { toolPattern, predicate, decision, source };
}

/** tool pattern 匹配 —— 精确 / ``.*`` 前缀 / ``*``（不支持 ``|`` OR；如要多模式拆多行）。 */
export function patternMatches(pattern: string, toolName: string): boolean {
  if (pattern === "*") return true;
  if (pattern.endsWith(".*")) {
    return toolName === pattern.slice(0, -2) || toolName.startsWith(pattern.slice(0, -1));
  }
  return pattern === toolName;
}
