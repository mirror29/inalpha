/**
 * services/data 的 Mastra tool 包装。
 *
 * Tool 设计遵循 [docs/05-tool-skill-discipline.md](../../../../docs/05-tool-skill-discipline.md)：
 * description 写"做什么 / 何时用 / 何时不用 / 坑"四要素。
 */
import { createTool } from "@mastra/core/tools";
import { z } from "zod";

import { mintServiceToken } from "../auth.js";
import { DataClient } from "../clients/data.js";
import { getSettings } from "../config.js";

const TimeframeSchema = z.enum(["1m", "5m", "15m", "1h", "4h", "1d"]);
const SymbolSchema = z
  .string()
  .regex(/^[A-Z0-9]+\/[A-Z0-9]+$/, "symbol 必须是 CCXT 风格 'BASE/QUOTE'，例如 BTC/USDT");

type ToolRequestContext = {
  /** forward 用户 JWT 给 data-service；缺省时 fallback 自签 service token（dev 友好）。 */
  authToken?: string;
};

async function getClient(ctx?: ToolRequestContext): Promise<DataClient> {
  const settings = getSettings();
  const token = ctx?.authToken ?? (await mintServiceToken({ sub: "service:orchestration" }));
  return new DataClient({ baseUrl: settings.dataServiceUrl, token });
}

/** backfill 专用长超时 client —— CCXT rate-limited fetch_ohlcv，大跨度可能分钟级 */
async function getBackfillClient(ctx?: ToolRequestContext): Promise<DataClient> {
  const settings = getSettings();
  const token = ctx?.authToken ?? (await mintServiceToken({ sub: "service:orchestration" }));
  return new DataClient({
    baseUrl: settings.dataServiceUrl,
    token,
    // 5 分钟：1 年 1m ≈ 2-3 分钟，1 个月 1m ≈ 10-20 秒；给到 300s 留余量
    timeoutMs: 300_000,
  });
}

// ────────────────────────────────────────────────────────────────────
// data.get_bars
// ────────────────────────────────────────────────────────────────────

export const dataGetBarsTool = createTool({
  id: "data.get_bars",
  description: `
    取已缓存的历史 K 线（从 TimescaleDB 读），支持 1m/5m/15m/1h/4h/1d。

    返回：按时间正序 (ts ASC) 的 bar 列表。**数组最后一根**是时间窗口内最新的一根。
    **limit 截断时取最新 N 根**（不是最早 N 根）—— 所以 limit=1 等价于"拿最新 1 根"。

    何时用：
    - 用户要看历史走势 / 做技术分析
    - 跑 backtest 前先确认数据范围
    - 给后续 factor 计算喂数据
    - **想取最近价格当 refPrice**：用 timeframe='1m', limit=5，省略 fromTs/toTs，
      读 bars[bars.length-1].close

    何时不用：
    - 想要新拉历史还没缓存的时段 → 先调 data.backfill_bars
    - 想要实时 tick → 等 D-9+ data.subscribe_ticks（WS）
    - 直接跑回测 → 用 paper.run_backtest（内部自动取 bars）

    坑：
    - DB 数据可能 stale —— D-8a 没自动同步最新行情，缓存最新 bar 可能落后几分钟到几小时
    - 数据库没缓存的时段返空数组（不会自动 backfill），用户要先 backfill
    - timeframe 必须是 enum 内值，自定义周期不支持
    - **不要把这个 tool 的结果脑补数字**——返回什么就是什么，LLM 不能"调整"价格
  `.trim(),
  inputSchema: z.object({
    venue: z.string().default("binance"),
    symbol: SymbolSchema,
    timeframe: TimeframeSchema.default("1h"),
    fromTs: z
      .string()
      .datetime()
      .optional()
      .describe("ISO 8601 起始时间；省略默认 = 当前时间 - 1 年"),
    toTs: z
      .string()
      .datetime()
      .optional()
      .describe("ISO 8601 结束时间；省略默认 = 当前时间"),
    limit: z.number().int().min(1).max(50_000).default(10_000),
  }),
  execute: async (inputData, ctx) => {
    const tc = ctx?.requestContext as ToolRequestContext | undefined;
    const client = await getClient(tc);

    const now = new Date();
    const fromTs = inputData.fromTs ?? new Date(now.getTime() - 365 * 86_400_000).toISOString();
    const toTs = inputData.toTs ?? now.toISOString();

    const bars = await client.getBars({
      venue: inputData.venue ?? "binance",
      symbol: inputData.symbol,
      timeframe: inputData.timeframe ?? "1h",
      fromTs,
      toTs,
      limit: inputData.limit ?? 10_000,
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
    - **想要"最新现价"但 data.get_bars 返回的是 stale 数据**（数小时前）→ backfill 最近 1 天

    何时不用：
    - 已有数据 → 直接 data.get_bars
    - 实时订阅 → 走 WS（D-9+）

    ⚠️  **数据量 × CCXT rate limit 速查表**（决定 from→to 跨度时参考）：
    -    1d / 1d  ≈  1 根     → 即时返回
    -   1m / 1h   ≈ 60 根     → ~1 秒
    -   1d / 1m   ≈ 1440 根   → ~2 秒
    -   1w / 1m   ≈ 10080 根  → ~10 秒
    -   1M / 1m   ≈ 43200 根  → ~40 秒
    -  **1y / 1m  ≈ 525600 根 → 2-3 分钟（接近 tool 5 分钟超时上限）**

    坑：
    - **不要"为了保险拉一年 1m"**——既慢又没必要。需要近期价用 1h timeframe + 1 周即可
    - venue 当前只支持 'binance'（D-7 阶段）
    - 不要在 LLM 单 turn 里循环 backfill 多个标的，分多次 tool call
    - tool 超时上限 5 分钟；超过这个跨度的 backfill 拆成多次小窗口调用
  `.trim(),
  inputSchema: z.object({
    venue: z.literal("binance").default("binance"),
    symbol: SymbolSchema,
    timeframe: TimeframeSchema.default("1h"),
    fromTs: z
      .string()
      .datetime()
      .optional()
      .describe("ISO 8601 起始；省略默认 = 当前 - 1 年"),
    toTs: z.string().datetime().optional().describe("ISO 8601 结束；省略默认 = 当前"),
  }),
  execute: async (inputData, ctx) => {
    const tc = ctx?.requestContext as ToolRequestContext | undefined;
    // backfill 专用长 timeout client（5 分钟）—— 默认 30s 对 CCXT 大跨度 fetch_ohlcv 不够
    const client = await getBackfillClient(tc);

    const now = new Date();
    const fromTs = inputData.fromTs ?? new Date(now.getTime() - 365 * 86_400_000).toISOString();
    const toTs = inputData.toTs ?? now.toISOString();

    return await client.backfillBars({
      venue: inputData.venue ?? "binance",
      symbol: inputData.symbol,
      timeframe: inputData.timeframe ?? "1h",
      fromTs,
      toTs,
    });
  },
});

export const dataTools = [dataGetBarsTool, dataBackfillBarsTool] as const;
