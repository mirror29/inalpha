/**
 * 玄学引擎 + tool 单测。
 *
 * 重点：**确定性**(同 seed 同结果)、结构合法、tool 返回带 disclaimer。
 */
import { afterEach, describe, expect, it, vi } from "vitest";

import { divinationApiRoutes } from "../src/divination/api.js";
import { castHexagram } from "../src/divination/hexagram.js";
import * as divinationRepo from "../src/divination/repo.js";
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

// ────────────────────────────────────────────────────────────────────
// 占卜台持久层 + HTTP 端点(用 mock Pool / fake ctx,不连真库)
// ────────────────────────────────────────────────────────────────────

/** 注入一个最小 Pool —— query 返回预设 rows,并记录调用。 */
function fakePool(rows: Record<string, unknown>[]): {
  query: ReturnType<typeof vi.fn>;
} {
  return { query: vi.fn(async () => ({ rows })) };
}

/** 拿某路由的 handler。 */
function route(path: string, method: string) {
  const r = divinationApiRoutes.find((x) => x.path === path && x.method === method);
  if (!r) throw new Error(`route not found: ${method} ${path}`);
  return r.handler;
}

/** 最小 hono Context —— 只实现 handler 用到的 req.json / req.query / req.param / json。 */
function fakeCtx(opts: {
  body?: unknown;
  query?: Record<string, string>;
  param?: Record<string, string>;
}): { ctx: never; result: () => { status: number; body: unknown } } {
  let status = 200;
  let body: unknown;
  const ctx = {
    req: {
      json: async () => {
        if (opts.body === undefined) throw new Error("no body");
        return opts.body;
      },
      query: (k: string) => opts.query?.[k],
      param: (k: string) => opts.param?.[k],
    },
    json: (obj: unknown, s = 200) => {
      body = obj;
      status = s;
      return { __captured: true } as unknown;
    },
  };
  return { ctx: ctx as never, result: () => ({ status, body }) };
}

describe("divination repo", () => {
  afterEach(() => divinationRepo.setPool(undefined));

  it("insertDivination 传 subject/mode/jsonb reading 并映射回行", async () => {
    const row = {
      id: "uuid-1",
      subject: "console:dev",
      mode: "hexagram",
      question: "q",
      symbol: null,
      kind: "hexagram",
      reading: { kind: "hexagram" },
      created_at: new Date("2026-06-08T00:00:00Z"),
    };
    const pool = fakePool([row]);
    divinationRepo.setPool(pool as never);
    const rec = await divinationRepo.insertDivination({
      subject: "console:dev",
      mode: "hexagram",
      question: "q",
      symbol: null,
      kind: "hexagram",
      reading: { kind: "hexagram" },
    });
    expect(rec.id).toBe("uuid-1");
    expect(rec.createdAt).toEqual(row.created_at);
    // 第 6 个参数是 JSON.stringify 后的 reading
    const args = pool.query.mock.calls[0][1] as unknown[];
    expect(args[0]).toBe("console:dev");
    expect(args[5]).toBe(JSON.stringify({ kind: "hexagram" }));
  });

  it("getDivination 带 subject 过滤,不存在返回 null", async () => {
    const pool = fakePool([]);
    divinationRepo.setPool(pool as never);
    const rec = await divinationRepo.getDivination("missing", "console:dev");
    expect(rec).toBeNull();
    const args = pool.query.mock.calls[0][1] as unknown[];
    expect(args).toEqual(["missing", "console:dev"]);
  });
});

describe("divination api · POST /divination/cast", () => {
  afterEach(() => divinationRepo.setPool(undefined));

  it("非法 mode → 400", async () => {
    const { ctx, result } = fakeCtx({ body: { mode: "nope", question: "q" } });
    await route("/divination/cast", "POST")(ctx);
    expect(result().status).toBe(400);
  });

  it("空 question → 400", async () => {
    const { ctx, result } = fakeCtx({ body: { mode: "hexagram", question: "  " } });
    await route("/divination/cast", "POST")(ctx);
    expect(result().status).toBe(400);
  });

  it("hexagram 直算落库,返回与引擎一致(确定性)+ 201", async () => {
    const insertSpy = vi
      .spyOn(divinationRepo, "insertDivination")
      .mockImplementation(async (input) => ({
        id: "uuid-1",
        createdAt: new Date("2026-06-08T00:00:00Z"),
        ...input,
      }));
    const { ctx, result } = fakeCtx({
      body: { mode: "hexagram", question: "BTC 能买吗", symbol: "BTC/USDT" },
    });
    await route("/divination/cast", "POST")(ctx);
    expect(result().status).toBe(201);
    // 落库的 reading 与直接调引擎一致(同 seed)
    const persisted = insertSpy.mock.calls[0][0];
    expect(persisted.kind).toBe("hexagram");
    expect(persisted.subject).toBe("console:dev");
    const engine = castHexagram("BTC 能买吗|BTC/USDT");
    expect((persisted.reading as { primary: unknown }).primary).toEqual(engine.primary);
    insertSpy.mockRestore();
  });

  it("tarotThree → kind=tarot 且 3 张", async () => {
    const insertSpy = vi
      .spyOn(divinationRepo, "insertDivination")
      .mockImplementation(async (input) => ({
        id: "uuid-2",
        createdAt: new Date("2026-06-08T00:00:00Z"),
        ...input,
      }));
    const { ctx } = fakeCtx({ body: { mode: "tarotThree", question: "运势" } });
    await route("/divination/cast", "POST")(ctx);
    const persisted = insertSpy.mock.calls[0][0];
    expect(persisted.kind).toBe("tarot");
    expect((persisted.reading as { cards: unknown[] }).cards).toHaveLength(3);
    insertSpy.mockRestore();
  });
});
