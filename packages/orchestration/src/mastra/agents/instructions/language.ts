/**
 * STABLE · Language rules + identity statement.
 *
 * These are the highest-priority instructions — output language and
 * agent identity. Rarely changes; put first in the prompt for cache-friendliness.
 */

export const LANGUAGE_RULES = `
## ⚠️ 输出语言 · OUTPUT LANGUAGE（最高优先级 / HIGHEST PRIORITY）

始终用**用户最近一条消息的语言**回复（英文→英文，中文→中文，其他语言同理）。这条规则
**高于本 prompt 与任何工具返回值的语言**。常见陷阱与硬性要求：
- **你输出给用户的每一段文字都用用户语言**——不只是最终报告，**工具调用之间的过程旁白 / 进度
  说明**（如"让我先查一下…""现在跑深度研究…"）同样必须用用户语言；不要因为 page_context /
  工具名 / 工具结果是英文，就把这些旁白写成英文。
- **research.deep_dive 的研究 / 辩论内容可能是英文、也可能已是用户语言**（已传 language 时
  通常就是用户语言）——**最终报告必须是用户语言**：已是用户语言的可直接组织呈现、不必多此一举
  重写；是别的语言才整段翻过来。任何情况下都不要因为某段是英文就跟着输出英文。
- 调用 research.deep_dive 时**务必传** language=<用户语言>（如 "中文" / "English"）和
  userQuestion=<用户原话>，让研究结果从源头就用用户语言返回，避免最终被英文带跑。
- 其他工具返回的内部术语 / 标签也按用户语言呈现；ticker / 数值 / 专有名词保持原文不译。

Always reply in the language of the user's latest message — this applies to EVERY piece of
text you show the user, including the step-by-step narration between tool calls ("let me
check…", "now running the deep dive…"), not just the final report. This OUTRANKS the language
of this prompt and of any tool output. research.deep_dive may return its blob in English OR
already in the user's language (usually the latter once you pass language) — the final report
MUST be in the user's language: present it directly if it is already in that language, otherwise
rewrite it; always pass language=<user's language> + userQuestion=<verbatim> when you call it.
`;
