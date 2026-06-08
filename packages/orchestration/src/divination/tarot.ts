/**
 * 塔罗引擎 —— 78 张牌确定性抽取(单张 / 三张牌阵)。
 *
 * **定位**：纯娱乐彩蛋。本模块只产出"牌面 + 正逆位关键词",
 * **绝不**产出价格 / 方向 / 买卖结论；叙事化解读交给 LLM 用用户语言生成。
 *
 * 牌库：22 张大阿尔卡纳 + 56 张小阿尔卡纳(权杖 / 圣杯 / 宝剑 / 星币 各 14 张)。
 * 正位 / 逆位由确定性 RNG 决定。三张牌阵按"过去 / 现在 / 未来"摆放。
 */

import { makeRng } from "./seed.js";

/** 牌阵类型。 */
export type TarotSpread = "single" | "three";

/** 一张牌的静态定义。 */
export interface TarotCardDef {
  /** 牌名(中文)。 */
  name: string;
  /** 牌名(英文)。 */
  english: string;
  /** 牌组：大阿尔卡纳 / 四小阿尔卡纳花色。 */
  arcana: "major" | "wands" | "cups" | "swords" | "pentacles";
  /** 正位关键词(趣味解读用)。 */
  upright: string[];
  /** 逆位关键词。 */
  reversed: string[];
}

/** 抽出的一张牌(含位置与正逆位)。 */
export interface DrawnCard extends TarotCardDef {
  /** 牌阵位置：单张 = "single"；三张 = past / present / future。 */
  position: "single" | "past" | "present" | "future";
  /** 是否逆位(注意：与 TarotCardDef.reversed 关键词数组区分,这里是朝向布尔)。 */
  isReversed: boolean;
}

/** 一次抽牌的结果。 */
export interface TarotReading {
  /** 判别用：本结果是塔罗。 */
  kind: "tarot";
  /** 牌阵类型。 */
  spread: TarotSpread;
  /** 抽出的牌(单张 1 张 / 三张牌阵 3 张)。 */
  cards: DrawnCard[];
}

/** 大阿尔卡纳 22 张。 */
const MAJOR: readonly TarotCardDef[] = [
  { name: "愚者", english: "The Fool", arcana: "major", upright: ["新的开始", "冒险", "纯真", "自由"], reversed: ["鲁莽", "犹豫", "风险失控"] },
  { name: "魔术师", english: "The Magician", arcana: "major", upright: ["创造", "意志", "资源齐备", "行动力"], reversed: ["操纵", "才不配位", "拖延"] },
  { name: "女祭司", english: "The High Priestess", arcana: "major", upright: ["直觉", "潜意识", "静观", "秘密"], reversed: ["压抑直觉", "信息隐藏", "迷失"] },
  { name: "皇后", english: "The Empress", arcana: "major", upright: ["丰饶", "滋养", "感性", "成长"], reversed: ["依赖", "停滞", "过度付出"] },
  { name: "皇帝", english: "The Emperor", arcana: "major", upright: ["权威", "结构", "掌控", "稳固"], reversed: ["专断", "僵化", "失序"] },
  { name: "教皇", english: "The Hierophant", arcana: "major", upright: ["传统", "信念", "指引", "规范"], reversed: ["叛逆", "教条", "另辟蹊径"] },
  { name: "恋人", english: "The Lovers", arcana: "major", upright: ["结合", "选择", "价值一致", "和谐"], reversed: ["失衡", "诱惑", "价值冲突"] },
  { name: "战车", english: "The Chariot", arcana: "major", upright: ["前进", "意志制胜", "掌舵", "决心"], reversed: ["失控", "方向不明", "受阻"] },
  { name: "力量", english: "Strength", arcana: "major", upright: ["内在力量", "勇气", "耐心", "柔克刚"], reversed: ["自我怀疑", "急躁", "力不从心"] },
  { name: "隐者", english: "The Hermit", arcana: "major", upright: ["内省", "独处", "寻找答案", "沉淀"], reversed: ["孤立", "逃避", "固执己见"] },
  { name: "命运之轮", english: "Wheel of Fortune", arcana: "major", upright: ["转机", "周期", "命运流转", "顺势"], reversed: ["逆势", "失运", "抗拒变化"] },
  { name: "正义", english: "Justice", arcana: "major", upright: ["公正", "因果", "权衡", "责任"], reversed: ["失衡", "偏颇", "推诿"] },
  { name: "倒吊人", english: "The Hanged Man", arcana: "major", upright: ["换位思考", "暂停", "舍得", "等待"], reversed: ["徒劳牺牲", "拖延", "执迷"] },
  { name: "死神", english: "Death", arcana: "major", upright: ["终结", "转化", "破旧立新", "蜕变"], reversed: ["抗拒改变", "停滞", "余烬未尽"] },
  { name: "节制", english: "Temperance", arcana: "major", upright: ["平衡", "调和", "节制", "耐心"], reversed: ["失调", "过度", "急于求成"] },
  { name: "恶魔", english: "The Devil", arcana: "major", upright: ["束缚", "欲望", "执念", "诱惑"], reversed: ["挣脱", "觉醒", "戒断"] },
  { name: "高塔", english: "The Tower", arcana: "major", upright: ["剧变", "崩塌", "觉醒", "突发"], reversed: ["将崩未崩", "勉强维持", "延迟的危机"] },
  { name: "星星", english: "The Star", arcana: "major", upright: ["希望", "疗愈", "灵感", "信心"], reversed: ["失望", "信心动摇", "迷茫"] },
  { name: "月亮", english: "The Moon", arcana: "major", upright: ["不确定", "幻象", "潜意识", "焦虑"], reversed: ["迷雾散去", "释怀", "真相浮现"] },
  { name: "太阳", english: "The Sun", arcana: "major", upright: ["光明", "成功", "活力", "喜悦"], reversed: ["暂时受挫", "过度乐观", "光芒被遮"] },
  { name: "审判", english: "Judgement", arcana: "major", upright: ["觉醒", "重生", "总结", "召唤"], reversed: ["自我怀疑", "逃避清算", "错过良机"] },
  { name: "世界", english: "The World", arcana: "major", upright: ["圆满", "完成", "整合", "成就"], reversed: ["未竟", "差一步", "拖沓"] },
];

/** 小阿尔卡纳花色配置(名称 + 关键词基调)。 */
const SUITS: {
  arcana: "wands" | "cups" | "swords" | "pentacles";
  zh: string;
  en: string;
}[] = [
  { arcana: "wands", zh: "权杖", en: "Wands" },
  { arcana: "cups", zh: "圣杯", en: "Cups" },
  { arcana: "swords", zh: "宝剑", en: "Swords" },
  { arcana: "pentacles", zh: "星币", en: "Pentacles" },
];

/** 小阿尔卡纳点数 / 宫廷牌名(1–10 + 侍从 / 骑士 / 王后 / 国王)。 */
const RANKS: { zh: string; en: string }[] = [
  { zh: "一", en: "Ace" },
  { zh: "二", en: "Two" },
  { zh: "三", en: "Three" },
  { zh: "四", en: "Four" },
  { zh: "五", en: "Five" },
  { zh: "六", en: "Six" },
  { zh: "七", en: "Seven" },
  { zh: "八", en: "Eight" },
  { zh: "九", en: "Nine" },
  { zh: "十", en: "Ten" },
  { zh: "侍从", en: "Page" },
  { zh: "骑士", en: "Knight" },
  { zh: "王后", en: "Queen" },
  { zh: "国王", en: "King" },
];

/**
 * 各花色按点数的关键词(正位)。逆位统一取"受阻 / 反向"基调,由 reversedHint 派生。
 * 关键词偏简洁,只为趣味解读提供锚点。
 */
const SUIT_KEYWORDS: Record<
  "wands" | "cups" | "swords" | "pentacles",
  { upright: string[]; reversedHint: string[] }
> = {
  wands: {
    upright: ["热情", "灵感", "新机", "稳基", "冲突", "胜利", "坚守", "迅捷", "韧性", "重担", "探索", "行动", "果敢", "领导"],
    reversedHint: ["热情消退", "犹疑", "受阻"],
  },
  cups: {
    upright: ["新感情", "联结", "庆祝", "厌倦", "失落", "怀旧", "幻想", "离开", "满足", "圆满", "心动", "浪漫", "共情", "包容"],
    reversedHint: ["情绪失衡", "封闭", "失望"],
  },
  swords: {
    upright: ["清晰", "抉择", "心碎", "休整", "失败", "过渡", "暗算", "受困", "焦虑", "结束", "好奇", "果断", "理性", "权威"],
    reversedHint: ["混乱", "误判", "释怀"],
  },
  pentacles: {
    upright: ["机遇", "权衡", "协作", "守财", "匮乏", "慷慨", "评估", "勤勉", "丰盈", "传承", "踏实", "投入", "务实", "稳健"],
    reversedHint: ["失衡", "拖延", "损耗"],
  },
};

/** 组装完整 78 张牌库(大阿尔卡纳 + 四花色小阿尔卡纳)。 */
function buildDeck(): TarotCardDef[] {
  const deck: TarotCardDef[] = [...MAJOR];
  for (const suit of SUITS) {
    const kw = SUIT_KEYWORDS[suit.arcana];
    RANKS.forEach((rank, i) => {
      deck.push({
        name: `${suit.zh}${rank.zh}`,
        english: `${rank.en} of ${suit.en}`,
        arcana: suit.arcana,
        upright: [kw.upright[i] ?? rank.zh],
        reversed: kw.reversedHint,
      });
    });
  }
  return deck;
}

/** 完整 78 张牌库(模块加载时构建一次)。 */
export const TAROT_DECK: readonly TarotCardDef[] = buildDeck();

/** 三张牌阵的位置标签(自左向右)。 */
const THREE_POSITIONS: DrawnCard["position"][] = ["past", "present", "future"];

/**
 * 确定性抽塔罗牌。
 *
 * @param seedStr seed 字符串(同 seed 必得同牌);一般是 `question` 拼 `symbol`
 * @param spread 牌阵：`single` 抽 1 张 / `three` 抽 3 张(过去 / 现在 / 未来),默认 single
 * @returns 抽牌结果(含正逆位)
 */
export function drawTarot(seedStr: string, spread: TarotSpread = "single"): TarotReading {
  const rng = makeRng(`tarot:${spread}:${seedStr}`);
  const count = spread === "three" ? 3 : 1;

  // 不放回抽样：洗一份索引池(Fisher-Yates),顺序取前 count 张。
  const pool = TAROT_DECK.map((_, i) => i);
  for (let i = pool.length - 1; i > 0; i -= 1) {
    const j = Math.floor(rng() * (i + 1));
    const tmp = pool[i]!;
    pool[i] = pool[j]!;
    pool[j] = tmp;
  }

  const cards: DrawnCard[] = [];
  for (let i = 0; i < count; i += 1) {
    const def = TAROT_DECK[pool[i]!]!;
    const isReversed = rng() < 0.5;
    const position: DrawnCard["position"] =
      spread === "three" ? (THREE_POSITIONS[i] ?? "present") : "single";
    cards.push({ ...def, position, isReversed });
  }

  return { kind: "tarot", spread, cards };
}
