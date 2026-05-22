/**
 * tool 名 matcher —— 精确 / 前缀通配 / OR 三种。
 *
 * 故意保持非常小的语法子集（ADR-0010 §matcher）：
 *
 * - ``"paper.run_backtest"``：精确匹配
 * - ``"paper.*"``：前缀匹配（仅尾部 ``.*``）
 * - ``"*"``：全匹配
 * - ``"paper.* | data.*"``：OR（``|`` 分隔的子模式，任意一个命中即可）
 *
 * 不支持中段通配（``data.*.bars``）/ 正则 / 集合等 —— 触发条件 > 当前 tool 数 100
 * 再考虑。
 */

/** 单一子模式匹配（不含 ``|``）。 */
function singleMatch(pattern: string, toolName: string): boolean {
  const p = pattern.trim();
  if (p === "*") return true;
  if (p.endsWith(".*")) {
    return toolName === p.slice(0, -2) || toolName.startsWith(p.slice(0, -1));
  }
  return p === toolName;
}

/** 给定 matcher 字符串与 toolName，判断是否命中。 */
export function toolMatches(matcher: string | undefined, toolName: string | undefined): boolean {
  // matcher 缺省 = 不限制（用于 SessionStart 等无 tool 事件，或全局 hook）
  if (!matcher) return true;
  if (!toolName) return false;

  const parts = matcher.split("|").map((s) => s.trim()).filter(Boolean);
  if (parts.length === 0) return true;

  for (const p of parts) {
    if (singleMatch(p, toolName)) return true;
  }
  return false;
}
