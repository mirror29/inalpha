/**
 * 玄学引擎 + tool 单测。
 *
 * 重点：**确定性**(同 seed 同结果)、结构合法、tool 返回带 disclaimer。
 */
import { describe, expect, it } from "vitest";

import { castHexagram } from "../src/divination/hexagram.js";
import { drawTarot, TAROT_DECK } from "../src/divination/tarot.js";
import {
  divinationCastHexagramTool,
  divinationDrawTarotTool,
} from "../src/tools/index.js";

/** 绕过 Mastra 1.x ctx 类型严格性的占位 ctx。 */
const ctx = (): never => ({ requestContext: {} }) as never;

describe("castHexagram", () => {
  it("同 seed 同卦(确定性)", () => {
    const a = castHexagram("BTC 能买吗|BTC/USDT");
    const b = castHexagram("BTC 能买吗|BTC/USDT");
    expect(a).toEqual(b);
  });

  it("不同 seed 一般得不同结果", () => {
    const a = castHexagram("问题甲");
    const b = castHexagram("问题乙");
    // 极小概率撞卦,但本卦 binary + 动爻组合应有别
    expect(
      a.primary.binary === b.primary.binary &&
        a.changingLines.join() === b.changingLines.join(),
    ).toBe(false);
  });

  it("结构合法：本卦 6 爻、binary 6 位、命中 64 卦", () => {
    const r = castHexagram("测试结构");
    expect(r.kind).toBe("hexagram");
    expect(r.primary.lines).toHaveLength(6);
    expect(r.primary.binary).toMatch(/^[01]{6}$/);
    expect(r.primary.number).toBeGreaterThanOrEqual(1);
    expect(r.primary.number).toBeLessThanOrEqual(64);
    expect(r.primary.judgment.length).toBeGreaterThan(0);
  });

  it("动爻与变卦一致：有动爻才有变卦,动爻位与 lines.changing 对应", () => {
    for (let i = 0; i < 50; i += 1) {
      const r = castHexagram(`seed-${i}`);
      const changingFromLines = r.primary.lines
        .filter((l) => l.changing)
        .map((l) => l.position);
      expect(r.changingLines).toEqual(changingFromLines);
      if (r.changingLines.length > 0) {
        expect(r.changed).not.toBeNull();
        expect(r.changed?.binary).toMatch(/^[01]{6}$/);
      } else {
        expect(r.changed).toBeNull();
      }
    }
  });
});

describe("drawTarot", () => {
  it("牌库 78 张", () => {
    expect(TAROT_DECK).toHaveLength(78);
  });

  it("同 seed 同牌(确定性)", () => {
    const a = drawTarot("今天运势", "three");
    const b = drawTarot("今天运势", "three");
    expect(a).toEqual(b);
  });

  it("single 抽 1 张,three 抽 3 张且位置为过去/现在/未来", () => {
    const single = drawTarot("q", "single");
    expect(single.cards).toHaveLength(1);
    expect(single.cards[0].position).toBe("single");

    const three = drawTarot("q", "three");
    expect(three.cards).toHaveLength(3);
    expect(three.cards.map((c) => c.position)).toEqual(["past", "present", "future"]);
  });

  it("三张不重复 + isReversed 是布尔 + 关键词非空", () => {
    const r = drawTarot("不重复测试", "three");
    const names = r.cards.map((c) => c.english);
    expect(new Set(names).size).toBe(3);
    for (const c of r.cards) {
      expect(typeof c.isReversed).toBe("boolean");
      const kw = c.isReversed ? c.reversed : c.upright;
      expect(kw.length).toBeGreaterThan(0);
    }
  });
});

describe("divination tools", () => {
  it("cast_hexagram 返回带 disclaimer + kind", async () => {
    const out = (await divinationCastHexagramTool.execute?.(
      { question: "测试", symbol: "BTC/USDT" } as never,
      ctx(),
    )) as { kind: string; disclaimer: string };
    expect(out.kind).toBe("hexagram");
    expect(out.disclaimer).toContain("非投资建议");
  });

  it("draw_tarot 返回带 disclaimer + 牌阵尺寸", async () => {
    const out = (await divinationDrawTarotTool.execute?.(
      { question: "测试", spread: "three" } as never,
      ctx(),
    )) as { kind: string; cards: unknown[]; disclaimer: string };
    expect(out.kind).toBe("tarot");
    expect(out.cards).toHaveLength(3);
    expect(out.disclaimer).toContain("非投资建议");
  });
});
