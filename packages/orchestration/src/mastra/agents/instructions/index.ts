/**
 * Prompt composition engine.
 *
 * Assembles instruction modules in **stability-tiered order** —— STABLE 内容全部
 * 放在前缀，唯一的动态内容（runtime_facts）放在**最末尾**，这样前缀缓存
 * （Anthropic cache_control 断点，或 DeepSeek/Kimi 这类按前缀自动命中的磁盘缓存）
 * 从第 0 字节到 STABLE 段末尾完全一致，可持续命中：
 *
 *   1. STABLE  — Language rules（最高优先级，语言规则）
 *   2. STABLE  — Tool catalog（工具目录）
 *   3. STABLE  — Decision pipeline（研究决策链路 + 质量门）
 *   4. STABLE  — Strategy protocol, order flow（策略协议 + 下单流）
 *   5. MARKET  — Venue routing, freshness（venue 路由 + 时效性）
 *   6. STABLE  — Page context, style, terminology（页面上下文 + 术语翻译）
 *   7. COND    — Divination rules（狐神签）
 *   8. SKILLS  — Skill catalog（ADR-0046 progressive disclosure）
 *   9. DYNAMIC — Runtime facts（**最末尾** · 每日变一次的日期注入）
 *
 * ⚠️ **cache 关键**：runtime_facts 必须在最后，且只用 **day 粒度** dateStr
 * （不用秒级 isoFull）——否则每次 invoke 时间戳变化会让整段前缀失效，
 * 后面所有 STABLE 层都命不中缓存（这是本次重构的核心目的，reviewer #128 指出）。
 *
 * @returns Full instructions string ready for the orchestrator system prompt
 */

import { LANGUAGE_RULES } from "./language.js";
import { TOOL_CATALOG } from "./tool-catalog.js";
import { DECISION_PIPELINE } from "./pipeline.js";
import { ORDER_AND_REFERENCE } from "./strategy.js";
import { MARKET_CONTEXT } from "./market.js";
import { STYLE_AND_TERMS } from "./style.js";
import { DIVINATION_RULES } from "./divination.js";

import { buildSkillsPromptSection } from "../../../skills/index.js";

/**
 * Assemble the complete orchestrator system prompt.
 *
 * Layer order is intentional — DO NOT reorder without understanding
 * the prompt cache implications. Stable layers first, volatile last.
 */
export function buildInstructions(): string {
  const now = new Date();
  // 只取 day 粒度——秒级时间戳会让前缀缓存每次 invoke 失效（reviewer #128）。
  const dateStr = now.toISOString().slice(0, 10);

  // ─── STABLE 前缀（从第 0 字节起完全一致，可持续命中缓存）──────────────

  // Layer 1 (STABLE · 最高优先级): 输出语言规则
  const language = LANGUAGE_RULES + "\n\n";

  // Layer 2 (STABLE · 能力目录): 工具描述
  const tools = TOOL_CATALOG + "\n\n";

  // Layer 3 (STABLE · 核心工作流): 研究决策链路 + 质量门
  const pipeline = DECISION_PIPELINE + "\n\n";

  // Layer 4 (STABLE · 执行规则): 下单流 + 策略协议 + 参考表
  const strategy = ORDER_AND_REFERENCE + "\n\n";

  // Layer 5 (MARKET · 半稳定): venue 路由 + 时效性 + 归因
  const market = MARKET_CONTEXT + "\n\n";

  // Layer 6 (STABLE · 面向用户): 页面上下文 + 语言风格 + 术语翻译
  const style = STYLE_AND_TERMS + "\n\n";

  // Layer 7 (COND · 目前恒含): 狐神签规则
  const divination = DIVINATION_RULES + "\n\n";

  // Layer 8 (COND · ADR-0046): skill 目录——memoized，无 skill 时为空串
  const skills = buildSkillsPromptSection();

  // ─── DYNAMIC 尾部（唯一每日变化处，放最后不破坏上面的缓存前缀）─────────

  // Layer 9 (DYNAMIC · 每日一变): runtime facts + 日期注入
  // 放在最末尾——day 粒度 dateStr 让这段一天内不变，跨天才失效一次。
  const runtimeFacts =
    `<runtime_facts>\n` +
    `Today (UTC) is ${dateStr}.\n\n` +
    `**Date handling rules**:\n` +
    `- Your training cutoff is months in the past; do NOT use your internal sense of "now".\n` +
    `- When the user says "近 30 天 / last 30 days / 最近 / 这周 / 本月" — **omit** ` +
    `\`from_ts\` / \`to_ts\` in tool inputs whenever the schema allows them to be optional. ` +
    `Server uses the real \`now\` as default.\n` +
    `- When the user gives an absolute date ("跑 2024 全年" / "from May 1 to today"), ` +
    `compute the range relative to ${dateStr}.\n` +
    `</runtime_facts>\n`;

  return (
    language +
    tools +
    pipeline +
    strategy +
    market +
    style +
    divination +
    skills +
    runtimeFacts
  );
}
