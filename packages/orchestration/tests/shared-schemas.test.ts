/**
 * shared/schemas.ts 单测 —— 校验共享领域 Schema 与 validateShape 契约。
 *
 * 目的：schemas.ts 目前尚无生产消费方，本测试给这份「后续 tool-view 类型安全
 * 渲染的地基」补上验证与防漂移网——任何人改坏 shape（如误删必填字段、改错
 * 类型）时立刻红。fixtures 尽量对齐真实 tool 输出形状（bar 取自 data.get_bars
 * 的 mock 形状，见 tools.test.ts）。
 */
import { describe, expect, it } from "vitest";

import {
  BacktestResultSchema,
  BarSchema,
  BarsResultSchema,
  FactorScoreResultSchema,
  FundamentalsSchema,
  TickerSchema,
  validateShape,
} from "../src/shared/schemas.js";

describe("shared/schemas", () => {
  it("BarSchema 接受合法 OHLCV，拒绝缺字段", () => {
    const bar = {
      ts: "2026-01-01T00:00:00Z",
      open: 100,
      high: 101,
      low: 99,
      close: 100.5,
      volume: 1.0,
    };
    expect(BarSchema.safeParse(bar).success).toBe(true);
    // 缺 volume → 必填校验失败
    const { volume: _drop, ...missing } = bar;
    expect(BarSchema.safeParse(missing).success).toBe(false);
  });

  it("BarsResultSchema 接受 venue/symbol/timeframe + bars 数组", () => {
    const result = {
      venue: "binance",
      symbol: "BTC/USDT",
      timeframe: "1h",
      bars: [
        { ts: "2026-01-01T00:00:00Z", open: 100, high: 101, low: 99, close: 100.5, volume: 1 },
      ],
    };
    expect(BarsResultSchema.safeParse(result).success).toBe(true);
  });

  it("TickerSchema 只强制 venue/symbol/price，其余可选", () => {
    expect(
      TickerSchema.safeParse({ venue: "binance", symbol: "BTC/USDT", price: 42000 }).success,
    ).toBe(true);
    // price 是字符串 → 类型不符
    expect(
      TickerSchema.safeParse({ venue: "binance", symbol: "BTC/USDT", price: "42000" }).success,
    ).toBe(false);
  });

  it("BacktestResultSchema 保留 sharpe_ci（promote 硬闸输入不被 strip）", () => {
    const parsed = BacktestResultSchema.safeParse({
      run_id: "run-1",
      sharpe: 1.2,
      sharpe_ci: { low: -0.1, high: 2.4, includes_zero: true },
      metrics: { sharpe: 1.2, total_trades: 30 },
    });
    expect(parsed.success).toBe(true);
    if (parsed.success) {
      expect(parsed.data.sharpe_ci?.includes_zero).toBe(true);
    }
  });

  it("FactorScoreResultSchema 校验 decay_state 枚举", () => {
    expect(
      FactorScoreResultSchema.safeParse({
        symbol: "BTC/USDT",
        timeframe: "1h",
        top: [{ name: "mom_20", decay_state: "fading" }],
      }).success,
    ).toBe(true);
    // 非法 decay_state
    expect(
      FactorScoreResultSchema.safeParse({
        symbol: "BTC/USDT",
        timeframe: "1h",
        top: [{ name: "mom_20", decay_state: "exploded" }],
      }).success,
    ).toBe(false);
  });

  it("FundamentalsSchema 全字段可选、indicators 为宽松 record", () => {
    expect(FundamentalsSchema.safeParse({}).success).toBe(true);
    expect(
      FundamentalsSchema.safeParse({
        symbol: "AAPL",
        indicators: { pe: 28.3, marketCap: 3.1e12 },
        categories: { valuation: ["pe", "pb"] },
      }).success,
    ).toBe(true);
  });

  it("validateShape 成功时返回类型安全 value", () => {
    const r = validateShape(BarSchema, {
      ts: "2026-01-01T00:00:00Z",
      open: 100,
      high: 101,
      low: 99,
      close: 100.5,
      volume: 1,
    });
    expect(r.ok).toBe(true);
    if (r.ok) expect(r.value.close).toBe(100.5);
  });

  it("validateShape 失败时返回带字段路径的 error", () => {
    const r = validateShape(BarSchema, { ts: "x", open: "not-a-number" });
    expect(r.ok).toBe(false);
    if (!r.ok) {
      expect(r.error).toContain("open");
      expect(r.error.length).toBeGreaterThan(0);
    }
  });
});
