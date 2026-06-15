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

/**
 * 玄学 tool 名(归一化为下划线形式)。
 *
 * 注意大小写之外的**点 / 下划线**两种形态都要认:
 *  - 历史回填(DB tool-invocation.toolName)是原始 tool id `divination.cast_hexagram`(点);
 *  - live AG-UI 把 id 里的 `.` 换成 `_` → `divination_cast_hexagram`(下划线,与
 *    tool-views 注册表同款约定)。
 * 早先这里只存点名 + 直接 has(name) → live 下划线名判 false,占卜在对话流里掉进裸 JSON
 * (占卜台是直渲染所以看着正常,掩盖了 chat 的 bug)。统一归一化后两种都命中。
 */
export const DIVINATION_TOOL_NAMES = new Set([
  "divination_cast_hexagram",
  "divination_draw_tarot",
]);

/** 给定 tool 名是否玄学 tool(点 / 下划线两种形态都认)。 */
export function isDivinationTool(name: string | undefined): boolean {
  return !!name && DIVINATION_TOOL_NAMES.has(name.replace(/\./g, "_"));
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
    if (!c || typeof c !== "object") continue;
    const obj = c as Record<string, unknown>;
    // cast 前做最小结构校验:只认 kind 不够 —— 若 message 包装层没展开导致
    // primary / cards 缺失,HexagramViz / TarotCards 会以 undefined 渲染崩溃。
    if (
      obj.kind === "hexagram" &&
      obj.primary &&
      typeof obj.primary === "object" &&
      Array.isArray((obj.primary as { lines?: unknown }).lines)
    ) {
      return c as DivinationView;
    }
    if (obj.kind === "tarot" && Array.isArray(obj.cards)) {
      return c as DivinationView;
    }
  }
  return null;
}
