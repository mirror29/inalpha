/**
 * services/paper 的 Mastra tool 包装。
 */
import { createTool } from "@mastra/core/tools";
import { z } from "zod";

import { mintServiceToken } from "../auth.js";
import { PaperClient } from "../clients/paper.js";
import { getSettings } from "../config.js";

// D-9 multi-market：与 tools/data.ts 保持一致——5 种 venue 全覆盖。
const TimeframeSchema = z.enum([
  "1m", "5m", "15m", "30m", "1h", "4h",
  "1d", "1wk", "1mo", "1q", "1y",
]);

const SymbolSchema = z
  .string()
  .min(1)
  .max(50)
  .regex(
    /^[\^A-Za-z0-9._/-]+$/,
    "symbol 不能为空 / 含空格；支持 crypto 'BTC/USDT' / 普通 'AAPL' / 指数 '^N225' / akshare 'sh.600519' / yfinance '005930.KS' / FRED 'DFF'",
  );

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
    - **D-9：跑 LLM 自创策略候选** —— 传 candidateId（来自 paper.author_strategy）而非 strategyId

    何时不用：
    - 实时跑模拟盘 → 用 paper.start_strategy（D-7 还没做）
    - 跨多标的批量 → 用 swarm.backtest_grid（D-7 还没做）
    - 单纯查 K 线走势 → data.get_bars

    坑：
    - paper 自动从 data-service 取 K 线；**没缓存先 data.backfill_bars**
      （报错 NO_BARS_AVAILABLE 时按 hint 操作）
    - params 是策略特定 dict，sma_cross 支持 fast_period / slow_period / trade_size
    - 报告里 num_trades=0 不一定是 bug，可能是趋势单边没触发交叉
    - **strategyId 与 candidateId 必须二选一**（都给 / 都不给 → 422）

    报告字段（D-7+）：
    - 基础：total_return_pct / num_trades / total_fees / final_equity / num_bars_processed
    - 绩效：sharpe / sortino / max_drawdown_pct / win_rate（数据不足时为 null）
    - D-9：fitness（多目标合成，ADR-0020）—— 排序候选用这个，不要用裸 Sharpe
    - equity_curve：[(ts, equity)] 序列，前端可直接画图
    - final_positions：结束时残留持仓（趋势策略可能持有到尾盘）
  `.trim(),
  inputSchema: z
    .object({
      strategyId: z
        .string()
        .optional()
        .describe("已注册策略 ID（用 paper.list_strategies 查；目前 sma_cross / mean_reversion / buy_and_hold）。与 candidateId 互斥。"),
      candidateId: z
        .string()
        .uuid()
        .optional()
        .describe("D-9 起：LLM 自创策略候选 UUID（paper.author_strategy 落库后）；与 strategyId 互斥。"),
      params: z
        .record(z.string(), z.unknown())
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
      researchId: z
        .string()
        .uuid()
        .optional()
        .describe(
          "D-8c 起：若本次回测由 research.deep_dive 驱动，把对应 research_id 透传过来；后续 trade.create_plan 可用同 research_id 关联血缘。",
        ),
      strategyHint: z
        .record(z.string(), z.unknown())
        .optional()
        .describe(
          "D-8c 起：触发本次回测的 strategy_hint（来自 compose_strategy.reasoning 上游）",
        ),
    })
    .superRefine((data, ctx) => {
      const hasId = typeof data.strategyId === "string" && data.strategyId.length > 0;
      const hasCand = typeof data.candidateId === "string" && data.candidateId.length > 0;
      if (hasId === hasCand) {
        ctx.addIssue({
          code: "custom",
          message: "必须给 strategyId 或 candidateId，二选一（不能同给也不能都不给）",
          path: ["strategyId"],
        });
      }
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
      candidateId: inputData.candidateId,
      params: inputData.params ?? {},
      venue: inputData.venue ?? "binance",
      symbol: inputData.symbol,
      timeframe: inputData.timeframe ?? "1h",
      fromTs,
      toTs,
      initialCash: inputData.initialCash ?? 10_000,
      feeRate: inputData.feeRate ?? 0.001,
      researchId: inputData.researchId,
      strategyHint: inputData.strategyHint,
    });
  },
});

// ────────────────────────────────────────────────────────────────────
// D-8c · paper.compose_strategy + paper.list_backtest_runs
// ────────────────────────────────────────────────────────────────────

const StrategyHintSchema = z.object({
  family: z.enum(["trend", "mean_reversion", "buy_hold", "none"]),
  params: z.record(z.string(), z.unknown()).default({}),
  reasoning: z.string().default(""),
});

const FactorInputSchema = z.object({
  name: z.string(),
  kind: z.enum(["momentum", "mean_reversion", "volatility", "macro", "sentiment"]),
  value: z.union([z.number(), z.string()]),
  strength: z.number().min(0).max(1),
  horizon: z.enum(["intraday", "swing", "position"]).default("swing"),
  explanation: z.string().default(""),
});

export const paperComposeStrategyTool = createTool({
  id: "paper.compose_strategy",
  description: `
    把 research.deep_dive 输出的 strategy_hint + factors 路由到内置 baseline 策略 +
    正规化参数。**D-9 起这是"快速通道"而非默认路径**——研究链路默认走 author_strategy。

    何时用（少数）：
    - 用户**明确点名**内置策略（"用 sma_cross 跑下 fast=5 slow=20"）
    - 用户**明确**要看 buy_and_hold 基线表现本身
    - sanity-check：想快速看 hint 对应的内置策略表现作直觉对照

    何时不用（默认情况）：
    - 任何"针对当下行情设计策略"的需求 → 走 paper.author_strategy（详见其 description）
    - hint.family === "none" → 不要硬走 compose；直接 author_strategy 根据 factors 写代码
    - 想要 buy_and_hold 作 alpha 对照 → **不需要**：run_backtest(candidateId=...) 自动并跑

    返回字段：
    - strategy_id：'sma_cross' / 'mean_reversion' / 'buy_and_hold'，或 null（拒绝）
    - params：可直接喂给 paper.run_backtest(strategyId=...) 的 params
    - reasoning：组装解释（reasonable 链路）
    - rejected_reason：non-null 表示拒绝——这种情况下应转 author_strategy，不是放弃
  `.trim(),
  inputSchema: z.object({
    hint: StrategyHintSchema,
    factors: z.array(FactorInputSchema).default([]),
    timeframe: TimeframeSchema.default("1h"),
  }),
  execute: async (inputData, ctx) => {
    const tc = ctx?.requestContext as ToolRequestContext | undefined;
    const client = await getClient(tc);
    // zod `.default()` 在 input 类型上保留 optional —— 显式补齐让 client 类型严格
    const hint = {
      family: inputData.hint.family,
      params: inputData.hint.params ?? {},
      reasoning: inputData.hint.reasoning ?? "",
    };
    const factors = (inputData.factors ?? []).map((f) => ({
      name: f.name,
      kind: f.kind,
      value: f.value,
      strength: f.strength,
      horizon: f.horizon ?? "swing",
      explanation: f.explanation ?? "",
    }));
    return await client.composeStrategy({
      hint,
      factors,
      timeframe: inputData.timeframe ?? "1h",
    });
  },
});

export const paperListBacktestRunsTool = createTool({
  id: "paper.list_backtest_runs",
  description: `
    查历史回测记录（按 research_id 或 strategy_code 过滤）。

    何时用：
    - 拿到 research 产物后想看"有没有人在同 research 下跑过回测"——避免重复算
    - 用户问"上次这个研究的回测结果"
    - 复盘策略表现，按 strategy_code 拉历史所有跑

    何时不用：
    - 想跑新回测 → paper.run_backtest（直接跑，run_id 落库自动产出）

    必须至少给 research_id 或 strategy_code 一个；同时给 → 优先用 research_id。

    返回字段：
    - run_id / params_hash：可作下游 trade.create_plan 的血缘锚点
    - metrics：{ sharpe, max_drawdown_pct, win_rate, total_return_pct, ... }
    - config：原回测请求参数
    - strategy_hint：触发本次回测的 hint dict（审计）
  `.trim(),
  inputSchema: z.object({
    researchId: z
      .string()
      .uuid()
      .optional()
      .describe("research.deep_dive 返回的 research_id"),
    strategyCode: z
      .string()
      .optional()
      .describe("策略注册表 key（如 'sma_cross'）"),
    limit: z.number().int().min(1).max(100).default(20),
  }),
  execute: async (inputData, ctx) => {
    if (!inputData.researchId && !inputData.strategyCode) {
      throw new Error(
        "paper.list_backtest_runs: must provide researchId or strategyCode (research_id 或 strategy_code 至少给一个)",
      );
    }
    const tc = ctx?.requestContext as ToolRequestContext | undefined;
    const client = await getClient(tc);
    return await client.listBacktestRuns({
      researchId: inputData.researchId,
      strategyCode: inputData.strategyCode,
      limit: inputData.limit ?? 20,
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

// ────────────────────────────────────────────────────────────────────
// D-8b 查询 tool
// ────────────────────────────────────────────────────────────────────

export const paperListOrdersTool = createTool({
  id: "paper.list_orders",
  description: `
    列出当前用户的订单流水（按 ts_event DESC，最近的在前）。

    何时用：
    - 用户问"我下过哪些单 / 今天交易记录 / 上次买 BTC 多少钱"
    - 复盘策略表现

    何时不用：
    - 查持仓 → paper.list_positions
    - 查账户总余额 → paper.get_account

    坑：
    - 按 account 隔离（用户身份从 JWT 提）
    - status 可选过滤：'FILLED' | 'REJECTED' | ...（不传则全部）
  `.trim(),
  inputSchema: z.object({
    symbol: SymbolSchema.optional().describe("可选按品种过滤，例如 'BTC/USDT'"),
    status: z.string().optional().describe("可选按 status 过滤"),
    limit: z.number().int().min(1).max(500).default(50),
  }),
  execute: async (inputData, ctx) => {
    const tc = ctx?.requestContext as ToolRequestContext | undefined;
    const client = await getClient(tc);
    return await client.listOrders({
      symbol: inputData.symbol,
      status: inputData.status,
      limit: inputData.limit ?? 50,
    });
  },
});

export const paperListPositionsTool = createTool({
  id: "paper.list_positions",
  description: `
    列出当前用户的活跃持仓（quantity != 0）。

    何时用：
    - 用户问"我现在持仓 / 我有多少 BTC / 我手上还有什么"

    返回：
    - quantity > 0 = 多头；< 0 = 空头
    - avg_open_price 是加权平均成本（已 reduce 过反向 fill）
    - realized_pnl 是历史已平仓累计盈亏

    何时不用：
    - 想看具体某笔单 → paper.list_orders
  `.trim(),
  inputSchema: z.object({
    includeFlat: z.boolean().default(false).describe("是否包含已平掉的（quantity=0 的历史持仓行）"),
  }),
  execute: async (inputData, ctx) => {
    const tc = ctx?.requestContext as ToolRequestContext | undefined;
    const client = await getClient(tc);
    return await client.listPositions(inputData.includeFlat ?? false);
  },
});

export const paperGetAccountTool = createTool({
  id: "paper.get_account",
  description: `
    当前账户快照：现金 / 初始本金 / 持仓估值 / 总权益 / 累计实现 PnL。

    何时用：
    - 用户问"我账户余额 / 我赚了多少 / 我账户总权益"

    坑：
    - 持仓估值用 avg_open_price 兜底（D-8b 不接实时 mark）；实际权益略偏保守
    - 默认初始 10000 USDT，首次下单时 lazy create
  `.trim(),
  inputSchema: z.object({}),
  execute: async (_input, ctx) => {
    const tc = ctx?.requestContext as ToolRequestContext | undefined;
    const client = await getClient(tc);
    return await client.getAccount();
  },
});

export const paperTools = [
  paperListStrategiesTool,
  paperRunBacktestTool,
  paperHealthTool,
  paperListOrdersTool,
  paperListPositionsTool,
  paperGetAccountTool,
  paperComposeStrategyTool,
  paperListBacktestRunsTool,
] as const;
