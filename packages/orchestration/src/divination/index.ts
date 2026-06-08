/**
 * 玄学引擎聚合导出(纯娱乐彩蛋)。
 *
 * 这些引擎确定性、无 service、无网络：只产出"卦象 / 牌面 + 静态典籍",
 * 不产出任何价格 / 方向结论。叙事化解读由 LLM 用用户语言生成。
 */
export { castHexagram } from "./hexagram.js";
export type {
  HexagramInfo,
  HexagramLine,
  HexagramReading,
} from "./hexagram.js";
export { drawTarot, TAROT_DECK } from "./tarot.js";
export type {
  DrawnCard,
  TarotCardDef,
  TarotReading,
  TarotSpread,
} from "./tarot.js";
export { makeRng, hashSeed, mulberry32 } from "./seed.js";

/**
 * 统一免责声明 —— 卦象 / 牌面永不进决策,复述时按用户语言转译。
 *
 * 单一来源:`tools/divination.ts`(LLM 会话占卜)与 `divination/api.ts`
 * (占卜台直算端点)都引用本常量,避免两处文案漂移。
 */
export const DIVINATION_DISCLAIMER =
  "仅作参照视角，非投资建议；落子仍归数据与风控 (a perspective for reference only, not investment advice)";
