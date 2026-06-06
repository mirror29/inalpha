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
