/**
 * services/paper 的 Mastra tool 包装。
 */
import { createTool } from "@mastra/core/tools";
import { z } from "zod";

import { mintServiceToken } from "../auth.js";
import { PaperClient } from "../clients/paper.js";
import { getSettings } from "../config.js";

const TimeframeSchema = z.enum(["1m", "5m", "15m", "1h", "4h", "1d"]);
const SymbolSchema = z
  .string()
  .regex(/^[A-Z0-9]+\/[A-Z0-9]+$/, "symbol 必须是 CCXT 风格 'BASE/QUOTE'");

type ToolRequestContext = { authToken?: string };

async function getClient(ctx?: ToolRequestContext): Promise<PaperClient> {
  const settings = getSettings();
  const token = ctx?.authToken ?? (await mintServiceToken({ sub: "service:orchestration" }));
  return new PaperClient({ baseUrl: settings.paperServiceUrl, token });
}

// ────────────────────────────────────────────────────────────────────
// paper.list_strategies
// ────────────────────────────────────────────────────────────────────

export const paperListStrategiesTool = createTool({
  id: "paper.list_strategies",
  description: `
    列出已注册的所有 strategy_id，给 paper.run_backtest 用。

    何时用：
    - 用户问"有哪些可用的策略"
    - 准备跑 backtest 但不确定 strategy_id 写啥

    何时不用：
    - 已知策略名直接用 → 不需要先列

    坑：D-7 起步只有 'sma_cross' 一个，后续会逐步添加
  `.trim(),
  inputSchema: z.object({}),
  execute: async (_input, ctx) => {
    const tc = ctx?.requestContext as ToolRequestContext | undefined;
    const client = await getClient(tc);
    return await client.listStrategies();
  },
});

// ────────────────────────────────────────────────────────────────────
// paper.run_backtest
// ────────────────────────────────────────────────────────────────────

export const paperRunBacktestTool = createTool({
  id: "paper.run_backtest",
  description: `
    用历史数据跑一次回测，**同步**返回完整报告（D-7：单次最长 ~1 分钟）。

    何时用：
    - 用户问"这个策略历史表现怎样"
    - 调参对比（fast/slow period 不同跑几次比一下）
    - 验证 entry/exit 信号触发频率

    何时不用：
    - 实时跑模拟盘 → 用 paper.start_strategy（D-7 还没做）
    - 跨多标的批量 → 用 swarm.backtest_grid（D-7 还没做）
    - 单纯查 K 线走势 → data.get_bars

    坑：
    - paper 自动从 data-service 取 K 线；**没缓存先 data.backfill_bars**
      （报错 NO_BARS_AVAILABLE 时按 hint 操作）
    - params 是策略特定 dict，sma_cross 支持 fast_period / slow_period / trade_size
    - 报告里 num_trades=0 不一定是 bug，可能是趋势单边没触发交叉

    报告字段（D-7+）：
    - 基础：total_return_pct / num_trades / total_fees / final_equity / num_bars_processed
    - 绩效：sharpe / sortino / max_drawdown_pct / win_rate（数据不足时为 null）
    - equity_curve：[(ts, equity)] 序列，前端可直接画图
    - final_positions：结束时残留持仓（趋势策略可能持有到尾盘）
  `.trim(),
  inputSchema: z.object({
    strategyId: z
      .string()
      .describe("已注册策略 ID（用 paper.list_strategies 查；目前只有 'sma_cross'）"),
    params: z
      .record(z.unknown())
      .default({})
      .describe("策略参数；sma_cross: { fast_period, slow_period, trade_size }"),
    venue: z.string().default("binance"),
    symbol: SymbolSchema,
    timeframe: TimeframeSchema.default("1h"),
    fromTs: z
      .string()
      .datetime()
      .optional()
      .describe("ISO 8601 起始时间；**省略时默认 = 当前时间往前回推 1 年**"),
    toTs: z
      .string()
      .datetime()
      .optional()
      .describe("ISO 8601 结束时间；**省略时默认 = 当前时间**"),
    initialCash: z.number().positive().default(10_000),
    feeRate: z.number().min(0).lt(1).default(0.001),
  }),
  execute: async (inputData, ctx) => {
    const tc = ctx?.requestContext as ToolRequestContext | undefined;
    const client = await getClient(tc);

    // 默认窗口：[now - 1y, now]。LLM 没指定 fromTs / toTs 时自动填，避免每次都要算时间。
    const now = new Date();
    const oneYearAgo = new Date(now.getTime() - 365 * 24 * 3600 * 1000);
    const fromTs = inputData.fromTs ?? oneYearAgo.toISOString();
    const toTs = inputData.toTs ?? now.toISOString();

    return await client.runBacktest({
      strategyId: inputData.strategyId,
      params: inputData.params ?? {},
      venue: inputData.venue ?? "binance",
      symbol: inputData.symbol,
      timeframe: inputData.timeframe ?? "1h",
      fromTs,
      toTs,
      initialCash: inputData.initialCash ?? 10_000,
      feeRate: inputData.feeRate ?? 0.001,
    });
  },
});

// ────────────────────────────────────────────────────────────────────
// paper.health
// ────────────────────────────────────────────────────────────────────

export const paperHealthTool = createTool({
  id: "paper.health",
  description: `
    探活 paper-service。LLM 一般不会主动调，主要供编排层 health check / 故障诊断用。
  `.trim(),
  inputSchema: z.object({}),
  execute: async (_input, ctx) => {
    const tc = ctx?.requestContext as ToolRequestContext | undefined;
    const client = await getClient(tc);
    return await client.health();
  },
});

export const paperTools = [
  paperListStrategiesTool,
  paperRunBacktestTool,
  paperHealthTool,
] as const;
