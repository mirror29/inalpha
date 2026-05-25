/**
 * ``inject-current-date`` —— SessionStart hook（D-9 fix）。
 *
 * **问题**：DeepSeek / GPT 系列模型训练 cutoff 通常落后真实时间 6-12 个月。被问到
 * "近 30 天回测"时，LLM 用训练时的"以为现在"算 from_ts，结果回测时段是过去某个月，
 * 与"用户真实当下"完全错位。用户感知：参数对得上，结果对不上。
 *
 * **修法**：SessionStart 注入今天日期 + 强引导，告诉 LLM 不要靠记忆猜日期。
 *
 * 注入文本含两层信息：
 *
 * 1. ``Today is YYYY-MM-DD (UTC)`` —— 硬事实
 * 2. **省略时间参数，让服务端按 now 默认** —— 比传错日期更安全
 *
 * SessionStart 的 ``additionalContext`` 会被 Mastra 拼到 system prompt 末尾；
 * DeepSeek 实测对位置敏感度低，放尾部也能用上。
 */
import type { HookHandler, HookRegistration } from "../types.js";

export type InjectCurrentDateOptions = {
  /** 自定义"当前时间"提供器，默认 ``() => new Date()``（测试 / 时间冻结用）。 */
  now?: () => Date;
};

export function createInjectCurrentDateHandler(
  opts: InjectCurrentDateOptions = {},
): HookHandler {
  const getNow = opts.now ?? (() => new Date());
  return () => {
    const now = getNow();
    const dateStr = now.toISOString().slice(0, 10);
    const isoFull = now.toISOString();
    const additionalContext =
      `<runtime_facts>\n` +
      `Today (UTC) is ${dateStr}. Full ISO: ${isoFull}.\n\n` +
      `**Date handling rules**:\n` +
      `- Your training cutoff is months in the past; do NOT use your internal sense of "now".\n` +
      `- When the user says "近 30 天 / last 30 days / 最近 / 这周 / 本月" — **omit** ` +
      `\`from_ts\` / \`to_ts\` in tool inputs whenever the schema allows. The server uses ` +
      `the real \`now\` as default.\n` +
      `- When the user gives an absolute date ("跑 2024 全年" / "from May 1 to today"), ` +
      `compute the range relative to ${dateStr}.\n` +
      `</runtime_facts>`;
    return { additionalContext };
  };
}

export function defaultInjectCurrentDateRegistration(
  opts: InjectCurrentDateOptions = {},
): HookRegistration {
  return {
    id: "inject-current-date",
    event: "SessionStart",
    handler: createInjectCurrentDateHandler(opts),
    blocking: false,
  };
}
