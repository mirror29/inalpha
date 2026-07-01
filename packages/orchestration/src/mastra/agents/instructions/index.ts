/**
 * Prompt composition engine.
 *
 * Assembles instruction modules in **stability-tiered order**
 * for maximum Anthropic prompt cache hit rate:
 *
 *   1. STABLE  — Tool catalog, decision pipeline, guardrails (rarely changes, ~500 lines)
 *   2. STABLE  — Strategy protocol, order flow, terminology (rarely changes, ~300 lines)
 *   3. MARKET  — Venue routing, freshness, multi-direction (changes per market, ~200 lines)
 *   4. STABLE  — Language rules + style (rarely changes but small, ~100 lines)
 *   5. DYNAMIC — Runtime facts (per-turn date injection, ~10 lines)
 *   6. SKILLS  — Conditional: progressive-disclosure skill catalog (ADR-0046)
 *   7. OPTIONAL— Divination rules (only when relevant — future optimization)
 *
 * The key insight (from OpenHands/LangGraph patterns):
 * STABLE content at the TOP → cache hits every turn.
 * VOLATILE content at the BOTTOM → only the tail changes.
 *
 * @param ctx Optional session context for future per-user customization
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
  const dateStr = now.toISOString().slice(0, 10);
  const isoFull = now.toISOString();

  // Layer 1 (DYNAMIC · per-turn): Runtime facts + date injection
  // Must be first in the final prompt so the LLM sees "today's date" before
  // the stable layers reference "now" / "as_of" concepts.
  const runtimeFacts =
    `<runtime_facts>\n` +
    `Today (UTC) is ${dateStr}. Full ISO: ${isoFull}.\n\n` +
    `**Date handling rules**:\n` +
    `- Your training cutoff is months in the past; do NOT use your internal sense of "now".\n` +
    `- When the user says "近 30 天 / last 30 days / 最近 / 这周 / 本月" — **omit** ` +
    `\`from_ts\` / \`to_ts\` in tool inputs whenever the schema allows them to be optional. ` +
    `Server uses the real \`now\` as default.\n` +
    `- When the user gives an absolute date ("跑 2024 全年" / "from May 1 to today"), ` +
    `compute the range relative to ${dateStr}.\n` +
    `</runtime_facts>\n\n`;

  // Layer 2 (STABLE · highest priority): Output language rules
  // Must be at the very top so no other section can override language behavior
  const language = LANGUAGE_RULES + "\n\n";

  // Layer 3 (STABLE · core identity + capability catalog): Tool descriptions
  // Changes only when tools are added/modified — high cache hit rate
  const tools = TOOL_CATALOG + "\n\n";

  // Layer 4 (STABLE · core workflow): Research decision pipeline + quality gate
  // The biggest stable block — cache-friendly placement after tool catalog
  const pipeline = DECISION_PIPELINE + "\n\n";

  // Layer 5 (STABLE · execution rules): Order flow, strategy protocol, reference tables
  const strategy = ORDER_AND_REFERENCE + "\n\n";

  // Layer 6 (MARKET · semi-volatile): Venue routing, freshness policy, attribution
  // Changes when new markets are added — separated from pure STABLE for easier diff
  const market = MARKET_CONTEXT + "\n\n";

  // Layer 7 (STABLE · user-facing): Page context, language/style, terminology
  const style = STYLE_AND_TERMS + "\n\n";

  // Layer 8 (CONDITIONAL · always included for now): Divination rules
  // Small enough (<30 lines) to include always; may become on-demand later
  const divination = DIVINATION_RULES + "\n\n";

  // Layer 9 (CONDITIONAL · ADR-0046): Skill catalog — progressive disclosure
  // Memoized: non-zero cost only on first call; empty string when no skills exist
  const skills = buildSkillsPromptSection();

  return (
    runtimeFacts +
    language +
    tools +
    pipeline +
    strategy +
    market +
    style +
    divination +
    skills
  );
}
