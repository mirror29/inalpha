/**
 * 玄学结果的前端类型 + 解析器。
 *
 * 这些类型**镜像** `divination.*` tool 的返回结构(权威定义在
 * packages/orchestration/src/divination)。前端只渲染 tool 给的 JSON,
 * **不重复**任何卦表 / 牌库数据——单一数据源仍在后端引擎。
 */

/** 六爻单爻读数。 */
export interface HexagramLineView {
  position: number;
  value: 6 | 7 | 8 | 9;
  yang: boolean;
  changing: boolean;
}

/** 一卦的静态信息。 */
export interface HexagramInfoView {
  number: number;
  name: string;
  english: string;
  binary: string;
  judgment: string;
}

/** 六爻起卦结果(tool 返回)。 */
export interface HexagramReadingView {
  kind: "hexagram";
  primary: HexagramInfoView & { lines: HexagramLineView[] };
  changed: HexagramInfoView | null;
  changingLines: number[];
  disclaimer?: string;
}

/** 抽出的一张塔罗牌。 */
export interface DrawnCardView {
  name: string;
  english: string;
  arcana: "major" | "wands" | "cups" | "swords" | "pentacles";
  upright: string[];
  reversed: string[];
  position: "single" | "past" | "present" | "future";
  isReversed: boolean;
}

/** 塔罗抽牌结果(tool 返回)。 */
export interface TarotReadingView {
  kind: "tarot";
  spread: "single" | "three";
  cards: DrawnCardView[];
  disclaimer?: string;
}

/** 任一玄学结果。 */
export type DivinationView = HexagramReadingView | TarotReadingView;

/** 玄学 tool 名(与 orchestration 注册一致)。 */
export const DIVINATION_TOOL_NAMES = new Set([
  "divination.cast_hexagram",
  "divination.draw_tarot",
]);

/** 给定 tool 名是否玄学 tool。 */
export function isDivinationTool(name: string | undefined): boolean {
  return !!name && DIVINATION_TOOL_NAMES.has(name);
}

/**
 * 把 tool-result 消息体(字符串)解析成玄学结果。
 *
 * tool 结果在 AG-UI 消息里是序列化后的字符串;不同 runtime 可能裹一层
 * `{ result: ... }`,这里都尝试一遍。解析失败 / 非玄学结构返回 null(调用方回退纯文本)。
 *
 * @param body tool-result 消息的纯文本(JSON 串)
 * @returns 玄学结果或 null
 */
export function parseDivination(body: string | undefined): DivinationView | null {
  if (!body) return null;
  let raw: unknown;
  try {
    raw = JSON.parse(body);
  } catch {
    return null;
  }
  // 逐层探测 kind:顶层 / .result / .data
  const candidates: unknown[] = [raw];
  if (raw && typeof raw === "object") {
    const obj = raw as Record<string, unknown>;
    if (obj.result) candidates.push(obj.result);
    if (obj.data) candidates.push(obj.data);
  }
  for (const c of candidates) {
    if (c && typeof c === "object") {
      const kind = (c as { kind?: unknown }).kind;
      if (kind === "hexagram" || kind === "tarot") {
        return c as DivinationView;
      }
    }
  }
  return null;
}
