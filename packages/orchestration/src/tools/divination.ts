/**
 * 玄学彩蛋 tool 集。
 *
 * Tool 设计遵循 [docs/05-tool-skill-discipline.md](../../../../docs/05-tool-skill-discipline.md)：
 * description 写"做什么 / 何时用 / 何时不用 / 坑"。
 *
 * **硬约束**：
 * - 纯娱乐：输出**永不**进决策(create_plan / approve / execute / factor / 回测)。
 * - 强制免责：每次返回带 `disclaimer`，提示"娱乐性质，非投资建议"。
 * - 确定性：引擎是本地 TS(无 service / 无网络 / 无 secret)，同 seed 同结果。
 * - 不产结论：只给卦象 / 牌面 + 静态典籍；价格 / 方向解读交给 LLM 用用户语言叙事。
 */
import { createTool } from "@mastra/core/tools";
import { z } from "zod";

import { castHexagram } from "../divination/hexagram.js";
import { drawTarot } from "../divination/tarot.js";

/** 统一免责声明 —— 跟随调用语境,LLM 复述时按用户语言转译。 */
const DISCLAIMER =
  "仅作参照视角，非投资建议；落子仍归数据与风控 (a perspective for reference only, not investment advice)";

// ────────────────────────────────────────────────────────────────────
// divination.cast_hexagram
// ────────────────────────────────────────────────────────────────────

export const divinationCastHexagramTool = createTool({
  id: "divination.cast_hexagram",
  description: `
    易经六爻起卦(金钱卦)——稻荷狐神签,方向犹豫时的参照视角。返回本卦 / 变卦 / 动爻 + 卦辞。

    何时用：
    - 用户**明确点名**要求签 / 占卜("求一卦 / 占一卦 / 起个卦 / cast a hexagram / 用易经看看")。
    - 用户在方向上犹豫不决,想要一个数据之外的参照角度松松气。

    何时不用：
    - **禁止作为任何决策依据**——真要判断买卖 / 择时走 research.deep_dive / factor.timing。
    - 用户没点名占卜时不要主动起卦(别在研究链路里偷偷插一脚)。
    - 不要把卦象展开成具体价格预测当事实结论。

    坑：
    - 同一 question(+symbol)起出的卦固定(确定性);想换一卦让用户换问法。
    - 返回带 disclaimer,复述给用户时务必带上"娱乐 / 非投资建议",并用**用户的语言**解读。
  `.trim(),
  inputSchema: z.object({
    question: z
      .string()
      .min(1)
      .max(200)
      .describe("用户想占问的事(自然语言);作为起卦 seed,同问得同卦"),
    symbol: z
      .string()
      .max(50)
      .optional()
      .describe("可选：相关标的(如 'BTC/USDT' / 'AAPL'),仅并入 seed,不做任何行情查询"),
  }),
  execute: async (inputData) => {
    const seed = `${inputData.question}|${inputData.symbol ?? ""}`;
    const reading = castHexagram(seed);
    return { ...reading, disclaimer: DISCLAIMER };
  },
});

// ────────────────────────────────────────────────────────────────────
// divination.draw_tarot
// ────────────────────────────────────────────────────────────────────

export const divinationDrawTarotTool = createTool({
  id: "divination.draw_tarot",
  description: `
    塔罗抽牌(78 张全套)——稻荷狐神签的另一种形式,方向犹豫时的参照视角。返回牌面 + 正逆位 + 关键词。

    何时用：
    - 用户**明确点名**要抽塔罗("抽张塔罗 / 来一张牌 / draw a tarot / 塔罗看看")。
    - 用户在当下处境犹豫,想要一个意象化的参照角度。

    何时不用：
    - **禁止作为任何决策依据**——真要判断买卖 / 择时走 research.deep_dive / factor.timing。
    - 用户没点名抽牌时不要主动抽。
    - 不要把牌面展开成具体价格预测当事实结论。

    坑：
    - spread='single' 抽 1 张;'three' 抽 3 张(过去 / 现在 / 未来)。
    - 同一 question(+symbol+spread)抽出的牌固定(确定性)。
    - 返回带 disclaimer,复述给用户时务必带上"娱乐 / 非投资建议",并用**用户的语言**解读。
  `.trim(),
  inputSchema: z.object({
    question: z
      .string()
      .min(1)
      .max(200)
      .describe("用户想占问的事(自然语言);作为抽牌 seed,同问得同牌"),
    spread: z
      .enum(["single", "three"])
      .default("single")
      .describe("牌阵：single 单张 / three 三张(过去-现在-未来)"),
    symbol: z
      .string()
      .max(50)
      .optional()
      .describe("可选：相关标的,仅并入 seed,不做任何行情查询"),
  }),
  execute: async (inputData) => {
    const seed = `${inputData.question}|${inputData.symbol ?? ""}`;
    const reading = drawTarot(seed, inputData.spread ?? "single");
    return { ...reading, disclaimer: DISCLAIMER };
  },
});

/** 玄学 tool 数组,给 tools/index.ts 聚合。 */
export const divinationTools = [
  divinationCastHexagramTool,
  divinationDrawTarotTool,
] as const;
