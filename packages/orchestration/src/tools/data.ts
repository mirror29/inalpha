/**
 * services/data 的 Mastra tool 包装。
 *
 * Tool 设计遵循 [docs/05-tool-skill-discipline.md](../../../../docs/05-tool-skill-discipline.md)：
 * description 写"做什么 / 何时用 / 何时不用 / 坑"四要素。
 */
import { createTool } from "@mastra/core/tools";
import { z } from "zod";

import { DataClient } from "../clients/data.js";
import { getSettings } from "../config.js";

const TimeframeSchema = z.enum(["1m", "5m", "15m", "1h", "4h", "1d"]);
const SymbolSchema = z
  .string()
  .regex(/^[A-Z0-9]+\/[A-Z0-9]+$/, "symbol 必须是 CCXT 风格 'BASE/QUOTE'，例如 BTC/USDT");

type ToolContext = {
  /** forward 用户 JWT 给 data-service。smoke / 后台用 mintServiceToken。 */
  authToken?: string;
};

function getClient(ctx?: ToolContext): DataClient {
  const settings = getSettings();
  const token = ctx?.authToken ?? "";
  if (!token) {
    throw new Error("data tool 需要 authToken（runtimeContext.authToken 或 mintServiceToken）");
  }
  return new DataClient({ baseUrl: settings.dataServiceUrl, token });
}

// ────────────────────────────────────────────────────────────────────
// data.get_bars
// ────────────────────────────────────────────────────────────────────

export const dataGetBarsTool = createTool({
  id: "data.get_bars",
  description: `
    取已缓存的历史 K 线（从 TimescaleDB 读），支持 1m/5m/15m/1h/4h/1d。

    何时用：
    - 用户要看历史走势 / 做技术分析
    - 跑 backtest 前先确认数据范围
    - 给后续 factor 计算喂数据

    何时不用：
    - 想要新拉历史还没缓存的时段 → 先调 data.backfill_bars
    - 想要实时 tick → 等 D-9+ data.subscribe_ticks（WS）
    - 直接跑回测 → 用 paper.run_backtest（内部自动取 bars）

    坑：
    - 数据库没缓存的时段返空数组（不会自动 backfill），用户要先 backfill
    - timeframe 必须是 enum 内值，自定义周期不支持
  `.trim(),
  inputSchema: z.object({
    venue: z.string().default("binance"),
    symbol: SymbolSchema,
    timeframe: TimeframeSchema.default("1h"),
    fromTs: z.string().datetime().describe("ISO 8601 起始时间，含"),
    toTs: z.string().datetime().describe("ISO 8601 结束时间，含"),
    limit: z.number().int().min(1).max(50_000).default(10_000),
  }),
  execute: async ({ context, runtimeContext }) => {
    const ctx = runtimeContext as ToolContext | undefined;
    const client = getClient(ctx);
    const bars = await client.getBars({
      venue: context.venue,
      symbol: context.symbol,
      timeframe: context.timeframe,
      fromTs: context.fromTs,
      toTs: context.toTs,
      limit: context.limit,
    });
    return { bars, count: bars.length };
  },
});

// ────────────────────────────────────────────────────────────────────
// data.backfill_bars
// ────────────────────────────────────────────────────────────────────

export const dataBackfillBarsTool = createTool({
  id: "data.backfill_bars",
  description: `
    从交易所拉历史 K 线落到 TimescaleDB。幂等（ON CONFLICT DO UPDATE）。

    何时用：
    - 准备跑 backtest 但数据库还没该时段的 K 线
    - 用户问"这个时段还没数据" → 先 backfill

    何时不用：
    - 已有数据 → 直接 data.get_bars
    - 实时订阅 → 走 WS（D-9+）

    坑：
    - venue 当前只支持 'binance'（D-7 阶段）
    - 跨度长（>1 个月 1m）一次拉时间几十秒，提示用户耐心
    - 不要在 LLM 单 turn 里循环 backfill 多个标的，分多次 tool call
  `.trim(),
  inputSchema: z.object({
    venue: z.literal("binance").default("binance"),
    symbol: SymbolSchema,
    timeframe: TimeframeSchema.default("1h"),
    fromTs: z.string().datetime(),
    toTs: z.string().datetime(),
  }),
  execute: async ({ context, runtimeContext }) => {
    const ctx = runtimeContext as ToolContext | undefined;
    const client = getClient(ctx);
    return await client.backfillBars({
      venue: context.venue,
      symbol: context.symbol,
      timeframe: context.timeframe,
      fromTs: context.fromTs,
      toTs: context.toTs,
    });
  },
});

export const dataTools = [dataGetBarsTool, dataBackfillBarsTool] as const;
