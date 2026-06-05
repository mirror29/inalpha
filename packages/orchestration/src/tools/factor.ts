/**
 * services/factor 的 Mastra tool 包装。
 *
 * 接现成因子库（pandas-ta / WorldQuant Alpha101 / qlib Alpha158）+ 自实现有效性打分
 * （前瞻收益分位 / 时序 Rank IC）。让 agent 能基于**经验证有效的因子**做分析与择时，
 * 而不是对着 5 个写死指标编叙事（见 docs/miro/11）。
 */
import { createTool } from "@mastra/core/tools";
import { z } from "zod";

import { mintServiceToken } from "../auth.js";
import { FactorClient } from "../clients/factor.js";
import { getSettings } from "../config.js";

// 只列 factor engine 真正支持的周期（_tf_seconds）。1mo/1q/1y 引擎不识别会按 1h 误算
// 窗口，且月/季/年线 bar 太少算不出有意义的有效性，故不暴露给 agent。
const TimeframeSchema = z.enum([
  "1m", "5m", "15m", "30m", "1h", "2h", "4h", "1d", "1wk",
]);

const SymbolSchema = z
  .string()
  .min(1)
  .max(50)
  .regex(
    /^[\^A-Za-z0-9._/-]+$/,
    "symbol 不能为空 / 含空格；crypto 'BTC/USDT' / 股票 'AAPL' / 指数 '^N225' / akshare 'sh.600519'",
  );

type ToolRequestContext = { authToken?: string };

async function getClient(ctx?: ToolRequestContext): Promise<FactorClient> {
  const settings = getSettings();
  const token = ctx?.authToken ?? (await mintServiceToken({ sub: "service:orchestration" }));
  return new FactorClient({ baseUrl: settings.factorServiceUrl, token });
}

// ────────────────────────────────────────────────────────────────────
// factor.timing —— 主力：当前对该标的有效的因子 + 方向
// ────────────────────────────────────────────────────────────────────

export const factorTimingTool = createTool({
  id: "factor.timing",
  description: `
    对一个标的 / 周期，返回**当前最有效的若干因子**（按时序 Rank IC 排序）及其读数、
    方向、强度。这是"用有效因子做择时"的主入口——给的是数据背书，不是 LLM 叙事。

    何时用：
    - 用户问"现在该不该买/卖""什么信号""怎么择时""有什么有效因子"
    - 设计策略 / 下单前，想知道"当下哪些因子真的预测了后市"（喂 author_strategy / create_plan 的依据）
    - 想用真因子值替代凭感觉的技术判断

    何时不用：
    - 只要 K 线原始走势 → data.get_bars
    - 要完整多 analyst 研究（基本面 + 情绪 + 辩论）→ research.deep_dive（factor.timing 是其中"技术有效性"那一块的加强版）
    - 全市场扫描 N 个标的 → 单次只查一个标的，别在 loop 里滥用

    返回 top_factors[]：每个含 name / kind / value（最新读数）/ rank_ic（越大越有效，正=因子高→后市涨）/
    direction（+1 看多 / -1 看空 / 0 无效）/ strength（0-1）/ low_confidence。
    available=false 或 top 为空时说明该标的样本不足，**别硬编故事**，如实告诉用户数据不够。

    坑：
    - rank_ic 是历史统计有效性，非未来保证；direction 只在 |rank_ic| 过阈值才非 0
    - lookbackBars 太小 → low_confidence；horizonBars 决定"预测多远的收益"（默认 5 根）
  `.trim(),
  inputSchema: z.object({
    venue: z.string().default("binance"),
    symbol: SymbolSchema,
    timeframe: TimeframeSchema.default("1h"),
    asOf: z
      .string()
      .datetime()
      .optional()
      .describe("评估截止时刻 ISO 8601（只用 <= asOf 的 bar）；省略=现在"),
    lookbackBars: z
      .number()
      .int()
      .min(120)
      .max(10000)
      .default(720)
      .describe("向前取多少根 bar 算有效性；越多越稳，太少会 low_confidence"),
    horizonBars: z
      .number()
      .int()
      .min(1)
      .max(60)
      .default(5)
      .describe("前瞻收益窗口（预测未来 N 根 bar 的累计收益）"),
    topN: z
      .number()
      .int()
      .min(1)
      .max(30)
      .optional()
      .describe("返回前几名有效因子；省略=服务端默认（约 10）"),
  }),
  execute: async (inputData, ctx) => {
    const tc = ctx?.requestContext as ToolRequestContext | undefined;
    const client = await getClient(tc);
    return await client.snapshot({
      venue: inputData.venue ?? "binance",
      symbol: inputData.symbol,
      timeframe: inputData.timeframe ?? "1h",
      asOf: inputData.asOf,
      lookbackBars: inputData.lookbackBars ?? 720,
      horizonBars: inputData.horizonBars ?? 5,
      topN: inputData.topN,
    });
  },
});

// ────────────────────────────────────────────────────────────────────
// factor.score —— 深挖：指定因子的完整有效性（分位前瞻收益 + ICIR）
// ────────────────────────────────────────────────────────────────────

export const factorScoreTool = createTool({
  id: "factor.score",
  description: `
    对**指定的一组因子**算完整有效性：时序 Rank IC、ICIR（稳定性）、分位前瞻收益、
    long-short。比 factor.timing 更细，用于深挖某几个因子到底灵不灵。

    何时用：
    - factor.timing 看到某因子有戏，想看它的分位收益结构 / 跨段稳定性
    - 用户点名某类因子（"RSI 现在有用吗""动量因子在这个币上灵不灵"）

    何时不用：
    - 只想要"当下该看哪些因子" → factor.timing（已自动排序取 top-N）
    - 不知道有哪些因子可选 → 先 factor.catalog

    factorIds 省略 = 算全部可时序计算因子（较慢）。建议先 catalog 选，再指定。
  `.trim(),
  inputSchema: z.object({
    venue: z.string().default("binance"),
    symbol: SymbolSchema,
    timeframe: TimeframeSchema.default("1h"),
    asOf: z.string().datetime().optional(),
    lookbackBars: z.number().int().min(120).max(10000).default(720),
    horizonBars: z.number().int().min(1).max(60).default(5),
    quantiles: z.number().int().min(2).max(10).default(5),
    factorIds: z
      .array(z.string())
      .optional()
      .describe("要算的因子 id（来自 factor.catalog）；省略=全部可时序因子"),
  }),
  execute: async (inputData, ctx) => {
    const tc = ctx?.requestContext as ToolRequestContext | undefined;
    const client = await getClient(tc);
    return await client.score({
      venue: inputData.venue ?? "binance",
      symbol: inputData.symbol,
      timeframe: inputData.timeframe ?? "1h",
      asOf: inputData.asOf,
      lookbackBars: inputData.lookbackBars ?? 720,
      horizonBars: inputData.horizonBars ?? 5,
      quantiles: inputData.quantiles ?? 5,
      factorIds: inputData.factorIds,
    });
  },
});

// ────────────────────────────────────────────────────────────────────
// factor.catalog —— 列出可用因子
// ────────────────────────────────────────────────────────────────────

export const factorCatalogTool = createTool({
  id: "factor.catalog",
  description: `
    列出因子库里所有因子定义（id / 来源 / kind / 是否需要 universe / 是否已启用）。

    何时用：
    - 用户问"有哪些因子可用""支持什么指标"
    - 用 factor.score 前先看有哪些 id 可选

    何时不用：
    - 只想知道"现在哪些因子有效" → factor.timing（直接给有效性排序，不用先 catalog）

    来源：pandas_ta（技术指标）/ alpha101（WorldQuant 101，部分横截面项 needs_universe=true 本期不算）/
    qlib_alpha158（默认未启用，available=false）。
  `.trim(),
  inputSchema: z.object({}),
  execute: async (_inputData, ctx) => {
    const tc = ctx?.requestContext as ToolRequestContext | undefined;
    const client = await getClient(tc);
    return await client.catalog();
  },
});

export const factorTools = [factorTimingTool, factorScoreTool, factorCatalogTool] as const;
