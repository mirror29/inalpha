/**
 * 易经六爻引擎 —— 金钱卦(三枚硬币)确定性起卦。
 *
 * **定位**：纯娱乐彩蛋。本模块只产出"卦象 + 静态典籍卦辞",
 * **绝不**产出价格 / 方向 / 买卖结论；叙事化解读交给 LLM 用用户语言生成。
 *
 * 起卦法(金钱卦)：每爻掷三枚硬币,正面记 3、反面记 2,三枚之和决定爻性 ——
 * - 6 = 老阴(变)：阴爻,变卦中翻成阳
 * - 7 = 少阳(不变)：阳爻
 * - 8 = 少阴(不变)：阴爻
 * - 9 = 老阳(变)：阳爻,变卦中翻成阴
 *
 * 六爻自下而上(初爻→上爻)。本卦由各爻阴阳定;变卦把动爻(6/9)翻面;
 * 无动爻则无变卦。卦名 / 卦辞按"下卦三爻 + 上卦三爻"的六位二进制查表得到。
 */

import { makeRng } from "./seed.js";

/** 单爻读数。 */
export interface HexagramLine {
  /** 爻位,1=初爻(最下) … 6=上爻(最上)。 */
  position: number;
  /** 金钱卦读数：6 老阴 / 7 少阳 / 8 少阴 / 9 老阳。 */
  value: 6 | 7 | 8 | 9;
  /** 是否阳爻(—);false 为阴爻(--)。 */
  yang: boolean;
  /** 是否动爻(老阴 6 / 老阳 9)。 */
  changing: boolean;
}

/** 一卦的静态信息(查表得到)。 */
export interface HexagramInfo {
  /** 周易卦序(1–64)。 */
  number: number;
  /** 卦名(中文)。 */
  name: string;
  /** 卦名(拼音 / 英文常用译名)。 */
  english: string;
  /** 六位二进制,自下而上;'1'=阳,'0'=阴。 */
  binary: string;
  /** 卦辞(简)。仅供趣味解读,非投资建议。 */
  judgment: string;
}

/** 一次完整起卦的结果。 */
export interface HexagramReading {
  /** 判别用：本结果是六爻卦。 */
  kind: "hexagram";
  /** 本卦(含逐爻读数)。 */
  primary: HexagramInfo & { lines: HexagramLine[] };
  /** 变卦；无动爻时为 null。 */
  changed: HexagramInfo | null;
  /** 动爻爻位(1–6),自下而上;无动爻为空数组。 */
  changingLines: number[];
}

/**
 * 64 卦表(周易卦序)。binary = 下卦三爻 + 上卦三爻,自下而上,'1'=阳/'0'=阴。
 * 八经卦：乾111 兑110 离101 震100 巽011 坎010 艮001 坤000(自下而上)。
 */
export const HEXAGRAMS: readonly HexagramInfo[] = [
  { number: 1, name: "乾", english: "Qian / The Creative", binary: "111111", judgment: "元亨利贞，自强不息。" },
  { number: 2, name: "坤", english: "Kun / The Receptive", binary: "000000", judgment: "厚德载物，先迷后得。" },
  { number: 3, name: "屯", english: "Zhun / Difficulty at the Beginning", binary: "100010", judgment: "始生之难，宜建侯而不宁。" },
  { number: 4, name: "蒙", english: "Meng / Youthful Folly", binary: "010001", judgment: "启蒙渐进，匪我求童蒙。" },
  { number: 5, name: "需", english: "Xu / Waiting", binary: "111010", judgment: "需于险中，有孚光亨，宜待时。" },
  { number: 6, name: "讼", english: "Song / Conflict", binary: "010111", judgment: "有孚窒惕，中吉终凶，不可成讼。" },
  { number: 7, name: "师", english: "Shi / The Army", binary: "010000", judgment: "师出以律，丈人吉无咎。" },
  { number: 8, name: "比", english: "Bi / Holding Together", binary: "000010", judgment: "比吉，原筮元永贞，亲辅相依。" },
  { number: 9, name: "小畜", english: "Xiao Chu / Small Taming", binary: "111011", judgment: "密云不雨，小有畜积，宜守。" },
  { number: 10, name: "履", english: "Lv / Treading", binary: "110111", judgment: "履虎尾，不咥人，谨行则亨。" },
  { number: 11, name: "泰", english: "Tai / Peace", binary: "111000", judgment: "小往大来，吉亨，天地交泰。" },
  { number: 12, name: "否", english: "Pi / Standstill", binary: "000111", judgment: "天地不交，闭塞之时，宜俭德避难。" },
  { number: 13, name: "同人", english: "Tong Ren / Fellowship", binary: "101111", judgment: "同人于野，亨，利涉大川。" },
  { number: 14, name: "大有", english: "Da You / Great Possession", binary: "111101", judgment: "元亨，柔得尊位，大中而上下应。" },
  { number: 15, name: "谦", english: "Qian / Modesty", binary: "001000", judgment: "谦亨，君子有终，受益。" },
  { number: 16, name: "豫", english: "Yu / Enthusiasm", binary: "000100", judgment: "利建侯行师，顺动则豫和。" },
  { number: 17, name: "随", english: "Sui / Following", binary: "100110", judgment: "随，元亨利贞，无咎，随时而动。" },
  { number: 18, name: "蛊", english: "Gu / Work on the Decayed", binary: "011001", judgment: "元亨，利涉大川，整治积弊。" },
  { number: 19, name: "临", english: "Lin / Approach", binary: "110000", judgment: "元亨利贞，至于八月有凶，宜及时。" },
  { number: 20, name: "观", english: "Guan / Contemplation", binary: "000011", judgment: "盥而不荐，有孚顒若，观以化人。" },
  { number: 21, name: "噬嗑", english: "Shi Ke / Biting Through", binary: "100101", judgment: "亨，利用狱，刚柔分而明断。" },
  { number: 22, name: "贲", english: "Bi / Grace", binary: "101001", judgment: "亨，小利有攸往，文饰得当。" },
  { number: 23, name: "剥", english: "Bo / Splitting Apart", binary: "000001", judgment: "不利有攸往，剥落之时，宜静待。" },
  { number: 24, name: "复", english: "Fu / Return", binary: "100000", judgment: "亨，出入无疾，反复其道，一阳来复。" },
  { number: 25, name: "无妄", english: "Wu Wang / Innocence", binary: "100111", judgment: "元亨利贞，不妄为则吉。" },
  { number: 26, name: "大畜", english: "Da Chu / Great Taming", binary: "111001", judgment: "利贞，不家食吉，利涉大川，蓄德待用。" },
  { number: 27, name: "颐", english: "Yi / Nourishment", binary: "100001", judgment: "贞吉，观颐自求口实，养正则吉。" },
  { number: 28, name: "大过", english: "Da Guo / Great Exceeding", binary: "011110", judgment: "栋桡，利有攸往，亨，非常之时。" },
  { number: 29, name: "坎", english: "Kan / The Abysmal", binary: "010010", judgment: "习坎，有孚维心亨，行有尚，险中守信。" },
  { number: 30, name: "离", english: "Li / The Clinging", binary: "101101", judgment: "利贞亨，畜牝牛吉，附丽得正。" },
  { number: 31, name: "咸", english: "Xian / Influence", binary: "001110", judgment: "亨，利贞，取女吉，感应相通。" },
  { number: 32, name: "恒", english: "Heng / Duration", binary: "011100", judgment: "亨，无咎，利贞，利有攸往，恒久之道。" },
  { number: 33, name: "遯", english: "Dun / Retreat", binary: "001111", judgment: "亨，小利贞，及时引退则吉。" },
  { number: 34, name: "大壮", english: "Da Zhuang / Great Power", binary: "111100", judgment: "利贞，壮而不妄，止于礼。" },
  { number: 35, name: "晋", english: "Jin / Progress", binary: "000101", judgment: "康侯用锡马蕃庶，昼日三接，进而上行。" },
  { number: 36, name: "明夷", english: "Ming Yi / Darkening of the Light", binary: "101000", judgment: "利艰贞，晦其明，内难而能正其志。" },
  { number: 37, name: "家人", english: "Jia Ren / The Family", binary: "101011", judgment: "利女贞，正家而天下定。" },
  { number: 38, name: "睽", english: "Kui / Opposition", binary: "110101", judgment: "小事吉，乖异之时，求同存异。" },
  { number: 39, name: "蹇", english: "Jian / Obstruction", binary: "001010", judgment: "利西南，不利东北，见险知止。" },
  { number: 40, name: "解", english: "Xie / Deliverance", binary: "010100", judgment: "利西南，险难初解，宜速宜早。" },
  { number: 41, name: "损", english: "Sun / Decrease", binary: "110001", judgment: "有孚元吉，损下益上，损而有节。" },
  { number: 42, name: "益", english: "Yi / Increase", binary: "100011", judgment: "利有攸往，利涉大川，损上益下。" },
  { number: 43, name: "夬", english: "Guai / Breakthrough", binary: "111110", judgment: "扬于王庭，孚号有厉，决而能和。" },
  { number: 44, name: "姤", english: "Gou / Coming to Meet", binary: "011111", judgment: "女壮，勿用取女，不期而遇宜慎。" },
  { number: 45, name: "萃", english: "Cui / Gathering Together", binary: "000110", judgment: "亨，王假有庙，聚之时大矣哉。" },
  { number: 46, name: "升", english: "Sheng / Pushing Upward", binary: "011000", judgment: "元亨，用见大人，勿恤，南征吉，积小成大。" },
  { number: 47, name: "困", english: "Kun / Oppression", binary: "010110", judgment: "亨，贞，大人吉无咎，困而不失其所亨。" },
  { number: 48, name: "井", english: "Jing / The Well", binary: "011010", judgment: "改邑不改井，养而不穷，汲古润今。" },
  { number: 49, name: "革", english: "Ge / Revolution", binary: "101110", judgment: "巳日乃孚，元亨利贞，悔亡，顺天应人。" },
  { number: 50, name: "鼎", english: "Ding / The Cauldron", binary: "011101", judgment: "元吉，亨，鼎新革故，养贤致用。" },
  { number: 51, name: "震", english: "Zhen / The Arousing", binary: "100100", judgment: "亨，震来虩虩，笑言哑哑，恐惧致福。" },
  { number: 52, name: "艮", english: "Gen / Keeping Still", binary: "001001", judgment: "艮其背，不获其身，止其所止，无咎。" },
  { number: 53, name: "渐", english: "Jian / Gradual Progress", binary: "001011", judgment: "女归吉，利贞，循序渐进则安。" },
  { number: 54, name: "归妹", english: "Gui Mei / The Marrying Maiden", binary: "110100", judgment: "征凶，无攸利，处非其位，宜慎。" },
  { number: 55, name: "丰", english: "Feng / Abundance", binary: "101100", judgment: "亨，王假之，宜日中，盛极宜守。" },
  { number: 56, name: "旅", english: "Lv / The Wanderer", binary: "001101", judgment: "小亨，旅贞吉，柔顺谨慎则免咎。" },
  { number: 57, name: "巽", english: "Xun / The Gentle", binary: "011011", judgment: "小亨，利有攸往，利见大人，顺以入。" },
  { number: 58, name: "兑", english: "Dui / The Joyous", binary: "110110", judgment: "亨利贞，和悦待人，刚中而柔外。" },
  { number: 59, name: "涣", english: "Huan / Dispersion", binary: "010011", judgment: "亨，王假有庙，利涉大川，涣散而后聚。" },
  { number: 60, name: "节", english: "Jie / Limitation", binary: "110010", judgment: "亨，苦节不可贞，节制有度则通。" },
  { number: 61, name: "中孚", english: "Zhong Fu / Inner Truth", binary: "110011", judgment: "豚鱼吉，利涉大川，诚信感物。" },
  { number: 62, name: "小过", english: "Xiao Guo / Small Exceeding", binary: "001100", judgment: "亨利贞，可小事，不可大事，宜下不宜上。" },
  { number: 63, name: "既济", english: "Ji Ji / After Completion", binary: "101010", judgment: "亨小，利贞，初吉终乱，盛极思危。" },
  { number: 64, name: "未济", english: "Wei Ji / Before Completion", binary: "010101", judgment: "亨，小狐汔济，濡其尾，将成未成宜慎终。" },
];

/** 六位二进制 → 卦信息,起卦查表用。 */
const BINARY_INDEX: ReadonlyMap<string, HexagramInfo> = new Map(
  HEXAGRAMS.map((h) => [h.binary, h]),
);

/**
 * 掷一爻：三枚硬币(各 2 或 3),返回金钱卦读数 6/7/8/9。
 *
 * @param rng [0,1) 生成器
 * @returns 6 老阴 / 7 少阳 / 8 少阴 / 9 老阳
 */
function tossLine(rng: () => number): 6 | 7 | 8 | 9 {
  let sum = 0;
  for (let i = 0; i < 3; i += 1) {
    sum += rng() < 0.5 ? 2 : 3;
  }
  return sum as 6 | 7 | 8 | 9;
}

/**
 * 确定性起一卦(金钱卦六爻)。
 *
 * @param seedStr seed 字符串(同 seed 必得同卦);一般是 `question` 拼 `symbol`
 * @returns 本卦 / 变卦 / 动爻的完整结果
 * @throws 永不抛错；任何 seed 都能起出合法卦
 */
export function castHexagram(seedStr: string): HexagramReading {
  const rng = makeRng(`hexagram:${seedStr}`);

  const lines: HexagramLine[] = [];
  let benBinary = "";
  let bianBinary = "";
  const changingLines: number[] = [];

  for (let i = 0; i < 6; i += 1) {
    const value = tossLine(rng);
    const yang = value === 7 || value === 9;
    const changing = value === 6 || value === 9;
    lines.push({ position: i + 1, value, yang, changing });
    benBinary += yang ? "1" : "0";
    // 变卦：动爻翻面(老阴 6 → 阳、老阳 9 → 阴),静爻不变
    bianBinary += changing ? (yang ? "0" : "1") : yang ? "1" : "0";
    if (changing) changingLines.push(i + 1);
  }

  const primaryInfo = BINARY_INDEX.get(benBinary);
  // 64 卦覆盖全部 6 位组合,理论上必命中；防御性兜底用乾卦。
  const primary: HexagramInfo = primaryInfo ?? HEXAGRAMS[0]!;
  const changed =
    changingLines.length > 0 ? (BINARY_INDEX.get(bianBinary) ?? null) : null;

  return {
    kind: "hexagram",
    primary: { ...primary, lines },
    changed,
    changingLines,
  };
}
