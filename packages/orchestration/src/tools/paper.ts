/**
 * services/paper 的 Mastra tool 包装。
 */
import { createTool } from "@mastra/core/tools";
import { z } from "zod";

import { defaultServiceSubject, mintServiceToken } from "../auth.js";
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
    /^[\^A-Za-z0-9._/\-:]+$/,
    "symbol 不能为空 / 含空格；支持 crypto 'BTC/USDT' / 普通 'AAPL' / 指数 '^N225' / akshare 'sh.600519' / yfinance '005930.KS' / FRED 'DFF'",
  );

type ToolRequestContext = { authToken?: string };

/**
 * perp（USDT-M 永续）回测入参——run_backtest / cv_backtest / check_sensitivity 共用，
 * 保证三个回测面口径一致（做空策略必须三处都用 perp，否则裸空被守门拒=0 成交=fitness 0）。
 */
const perpInputFields = {
  tradingMode: z
    .enum(["spot", "perp"])
    .default("spot")
    .describe(
      "spot（默认，现货 long-only）或 perp（USDT-M 永续 + 逐仓，放开做空 / 杠杆）。" +
      "**做空策略必须用 perp**——spot 下做空 SELL 被守门拒，会 0 成交、看着像坏策略。" +
      "perp 仅 crypto 永续标的（ccxt 记法 BTC/USDT:USDT，非现货 BTC/USDT）。",
    ),
  leverage: z
    .number()
    .int()
    .min(1)
    .max(20)
    .default(1)
    .describe("杠杆倍数（perp 用，1..20）；spot 恒 1"),
  fundingRate: z
    .number()
    .default(0)
    .describe("perp 用的（常数）资金费率，每结算时点计提；0=不计 funding（默认）。正费率多头付空头"),
} as const;

async function getClient(ctx?: ToolRequestContext): Promise<PaperClient> {
  const settings = getSettings();
  const token = ctx?.authToken ?? (await mintServiceToken({ sub: defaultServiceSubject() }));
  return new PaperClient({ baseUrl: settings.paperServiceUrl, token });
}

/**
 * 回测类工具专用长超时 client —— 缓存命中（增量 backfill）多为秒级，但空缓存首次
 * 全量拉数据（CCXT rate-limited fetch_ohlcv）+ CV/敏感性跑多路引擎可能分钟级，
 * 默认 30s 会 `request timed out`（与 tools/data.ts getBackfillClient 同模式）。
 */
async function getBacktestClient(
  ctx?: ToolRequestContext,
  timeoutMs = 300_000,
): Promise<PaperClient> {
  const settings = getSettings();
  const token = ctx?.authToken ?? (await mintServiceToken({ sub: defaultServiceSubject() }));
  return new PaperClient({ baseUrl: settings.paperServiceUrl, token, timeoutMs });
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
// paper.list_archetypes（D-12 · ADR-0051 D1）
// ────────────────────────────────────────────────────────────────────

export const paperListArchetypesTool = createTool({
  id: "paper.list_archetypes",
  description: `
    列出策略原型库——经验证、过沙盒协议、可参数化的 Strategy 骨架，给 author_strategy 当**起点**。

    何时用：
    - **写策略前**（研究链路第 2 步：拿到 factor.timing 的 stable 因子 + kind 之后）
    - 按主因子 kind 传 factorKinds，取匹配骨架；以骨架 code 为起点**按因子证据改参/改逻辑**
      再走 paper.author_strategy（骨架降低协议踩坑 + 给结构，但逻辑仍要定制）

    何时不用：
    - 用户明确点名内置策略（"用 sma_cross"）→ 走 paper.compose_strategy
    - 纯调已有候选的参数 → 直接 paper.run_backtest

    坑：
    - 骨架是**起点不是终点**，更不是绕过验证——改完仍走 author_strategy 过沙盒三审
    - 骨架参数默认值是初值参考，别原样套用钉死；不保证 alpha
    - 现货 long-only；返回 code 已是"候选源码"形态（无 import，直接用注入符号）

    返回：{ archetypes: [{ name, applies_to_kinds, description, when_to_use,
    when_not_to_use, failure_modes, compatible_pivots, params, code }] }
    （传 factorKinds 时匹配 kind 的排前面）
  `.trim(),
  inputSchema: z.object({
    factorKinds: z
      .array(z.string())
      .optional()
      .describe(
        "因子 kind 列表（来自 factor.timing 的 top 因子 kind，如 ['momentum','trend']）；" +
          "匹配的骨架排前面。省略则返回全部。",
      ),
  }),
  execute: async (input, ctx) => {
    const tc = ctx?.requestContext as ToolRequestContext | undefined;
    const client = await getClient(tc);
    return await client.listArchetypes(input.factorKinds);
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
    - 实时跑模拟盘（promoted 候选按行情自动跑）→ 用 paper.start_strategy
    - **跨多标的 / 多候选 批量** → 用 swarm.run_backtest_grid（D-9 已支持全市场）
    - 单纯查 K 线走势 → data.get_bars

    坑：
    - paper 自动从 data-service 取 K 线；**没缓存先 data.backfill_bars**
      （报错 NO_BARS_AVAILABLE 时按 hint 操作）
    - params 是策略特定 dict，sma_cross 支持 fast_period / slow_period / trade_size
    - 报告里 num_trades=0 不一定是 bug，可能是趋势单边没触发交叉
     - **position sizing: runner 自动注入 position_pct=1.0（满仓）；需要调仓位比例时在 params 里显式传 position_pct（0.0-1.0）**
     - **strategyId 与 candidateId 必须二选一**（都给 / 都不给 → 422）

    报告字段（D-7+）：
    - 基础：total_return_pct / num_trades / total_fees / final_equity / num_bars_processed
    - 绩效：sharpe / sortino / max_drawdown_pct / win_rate（数据不足时为 null）
    - 框架兜底：protective_exits（ADR-0052 框架级持仓保护止损本次触发的平仓笔数）——
      >0 说明灾难兜底生效过几次；这是回测对"未来 live 也会有的兜底"的如实反映，
      可向用户说明风险被框架封住了几次（默认 -20% 硬止损，非策略自带）
    - D-9：fitness（多目标合成，ADR-0020）—— 排序候选用这个，不要用裸 Sharpe
    - D-12（ADR-0027）：sharpe_ci { lower, upper, includes_zero }（年化 Sharpe 95% 置信区间）——
      **防"看起来好"的关键字段**。includes_zero=true ⇒ Sharpe 统计上不显著为正，
      回测曲线好看但禁不起重采样检验，**不要把 Sharpe 当卖点**，必须如实告诉用户
    - equity_curve：[(ts, equity)] 序列；**超 120 点会等距降采样**（带
      equity_curve_downsampled_from 标原始点数），看形状趋势用，精确逐点分析
      不要从这里取（完整曲线在 paper 服务 API）
    - final_positions：结束时残留持仓（趋势策略可能持有到尾盘）
    - **D-12 validation（holdout 验证，默认开）**：前 70% train + 后 30% holdout
      各自的 sharpe/return/mdd/num_trades + decay_ratio（holdout_sharpe/train_sharpe）。
      **解读纪律**：
      · decay_ratio < 0.5 或 holdout.sharpe < 0 → 过拟合信号，下一版**减参数/简化逻辑**
        而不是加逻辑
      · holdout_sharpe_ci_includes_zero=true → holdout 收益统计上不显著为正
      · flags 含 insufficient_sample → 衰减比不可靠，扩窗口再跑
      · **调参看 train 段，holdout 只作裁判**——反复对着 holdout 调参 = 间接过拟合 holdout
      · 报给用户的结论必须引用 holdout（别只报全窗 Sharpe 当"历史表现"）
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
        .describe("策略参数；sma_cross: { fast_period, slow_period, trade_size, position_pct }；mean_reversion: { period, std_mult, trade_size, position_pct }；position_pct 默认 1.0（满仓），设为 0.0-1.0 间值调仓位比例"),
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
      tradingMode: z
        .enum(["spot", "perp"])
        .default("spot")
        .describe(
          "spot（默认，现货 long-only）或 perp（USDT-M 永续 + 逐仓，放开做空 / 杠杆）。" +
          "**做空策略必须用 perp 回测**——spot 下做空 SELL 被守门拒，回测会 0 成交、看着像坏策略。" +
          "perp 仅 crypto 永续标的（ccxt 记法 BTC/USDT:USDT，非现货 BTC/USDT）。",
        ),
      leverage: z
        .number()
        .int()
        .min(1)
        .max(20)
        .default(1)
        .describe("杠杆倍数（perp 用，1..20）；spot 恒 1"),
      fundingRate: z
        .number()
        .default(0)
        .describe("perp 回测用的（常数）资金费率，每结算时点计提；0=不计 funding（默认）。正费率多头付空头"),
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
    // 长超时：空缓存首次全量拉数据可分钟级（增量 backfill 命中时秒级）
    const client = await getBacktestClient(tc);

    // 默认窗口：[now - 1y, now]。LLM 没指定 fromTs / toTs 时自动填，避免每次都要算时间。
    const now = new Date();
    const oneYearAgo = new Date(now.getTime() - 365 * 24 * 3600 * 1000);
    const fromTs = inputData.fromTs ?? oneYearAgo.toISOString();
    const toTs = inputData.toTs ?? now.toISOString();

    const report = await client.runBacktest({
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
      tradingMode: inputData.tradingMode,
      leverage: inputData.leverage,
      fundingRate: inputData.fundingRate,
      researchId: inputData.researchId,
      strategyHint: inputData.strategyHint,
    });
    return downsampleEquityCurves(report);
  },
});

/** tool 输出里 equity_curve 的保留点数上限（首尾必留，中间等距抽样）。 */
const EQUITY_CURVE_MAX_POINTS = 120;

/**
 * 把回测报告里的 equity_curve（含 baseline 的）降采样后再进 LLM context。
 *
 * 背景（2026-06-11 实测事故）：1 年 × 1h 回测的曲线 ~8760 点 ≈ 500KB ≈ 15 万 token，
 * 原样进消息历史 + memory 窗口 50 条 → 几次回测就把 DeepSeek 1M 上下文撑爆
 * （AI_APICallError: maximum context length），流中断、对话线程报废。
 *
 * 曲线形状对 LLM 判读 120 点足够；完整曲线前端走 paper 服务 API 拉，不走 chat。
 */
function downsampleEquityCurves<T>(report: T): T {
  if (!report || typeof report !== "object") return report;
  const r = report as Record<string, unknown>;
  const out: Record<string, unknown> = { ...r };
  for (const key of ["equity_curve", "equityCurve"]) {
    const curve = out[key];
    if (Array.isArray(curve) && curve.length > EQUITY_CURVE_MAX_POINTS) {
      const step = (curve.length - 1) / (EQUITY_CURVE_MAX_POINTS - 1);
      out[key] = Array.from(
        { length: EQUITY_CURVE_MAX_POINTS },
        (_, i) => curve[Math.round(i * step)],
      );
      out[`${key}_downsampled_from`] = curve.length;
    }
  }
  // baseline 子报告同样处理（candidate 回测自动并跑 buy_and_hold）
  if (out.baseline && typeof out.baseline === "object") {
    out.baseline = downsampleEquityCurves(out.baseline);
  }
  return out as T;
}

// ────────────────────────────────────────────────────────────────────
// D-12 · paper.check_sensitivity
// ────────────────────────────────────────────────────────────────────

export const paperCheckSensitivityTool = createTool({
  id: "paper.check_sensitivity",
  description: `
    参数邻域敏感性检查：对最终参数的每个数值参数做 one-at-a-time ±20% 扰动，
    base + 邻域（≤16 组）各跑一次回测，返回邻域 fitness 分布 + verdict。

    何时用：
    - **promote 前必跑**——verdict=cliff（邻域最差 < 0.5×base）= 参数尖峰 =
      这套参数恰好踩中历史，**不应 promote**
    - 迭代中怀疑某版"好得可疑"时做体检

    何时不用：
    - 策略还没跑出 fitness > baseline（先把策略本身改及格，敏感性无意义——
      base fitness ≤ 0 时 verdict 恒为 insufficient）
    - 想做参数搜索/调优 → 这不是网格搜索工具，邻域结果只用来判稳健，
      **不要**拿邻域里最好的组合当新参数（那是对着噪声调参）

    坑：
    - params 必须传**最终收敛的完整参数 dict**——源码里的默认值不在扰动范围
    - trade_size / position_pct 不扰动（只缩放仓位不改信号）；bool/字符串跳过
    - 邻域 run 不落 backtest_runs（不污染回测历史）；candidate 路径摘要自动写
      candidate.metrics.sensitivity 供 promote 审计
    - verdict=insufficient（成功邻域 < 4 组）→ 结论不可靠，看 neighbors 里的
      error 修参数边界后重跑

    解读：
    - robust：参数面是高原，小扰动不掉崖 → 可进入 promote 流程
    - cliff：报告里必须向用户明示"参数敏感，过拟合风险"，建议减参数/简化逻辑
  `.trim(),
  inputSchema: z
    .object({
      strategyId: z.string().optional().describe("内置策略 ID；与 candidateId 互斥"),
      candidateId: z.string().uuid().optional().describe("候选 UUID；与 strategyId 互斥"),
      params: z
        .record(z.string(), z.unknown())
        .describe("最终收敛的完整参数 dict（数值参数将被 ±20% 扰动）"),
      venue: z.string().default("binance"),
      symbol: SymbolSchema,
      timeframe: TimeframeSchema.default("1h"),
      fromTs: z.string().datetime().describe("ISO 8601 起始时间（与最终回测同窗口）"),
      toTs: z.string().datetime().describe("ISO 8601 结束时间"),
      initialCash: z.number().positive().default(10_000),
      feeRate: z.number().min(0).lt(1).default(0.001),
      ...perpInputFields,
      pct: z.number().gt(0).lt(1).default(0.2).describe("扰动幅度（默认 ±20%）"),
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
    // 敏感性跑 base + ≤16 邻域各一次回测，长超时兜底
    const client = await getBacktestClient(tc, 600_000);
    return await client.checkSensitivity({
      strategyId: inputData.strategyId,
      candidateId: inputData.candidateId,
      params: inputData.params,
      venue: inputData.venue ?? "binance",
      symbol: inputData.symbol,
      timeframe: inputData.timeframe ?? "1h",
      fromTs: inputData.fromTs,
      toTs: inputData.toTs,
      initialCash: inputData.initialCash ?? 10_000,
      feeRate: inputData.feeRate ?? 0.001,
      tradingMode: inputData.tradingMode,
      leverage: inputData.leverage,
      fundingRate: inputData.fundingRate,
      pct: inputData.pct ?? 0.2,
    });
  },
});

export const paperCvBacktestTool = createTool({
  id: "paper.cv_backtest",
  description: `
    多路径时序交叉验证回测（ADR-0028）：把策略在多条样本外路径上跑一遍，返回 OOS Sharpe
    分布（p5/p50/p95）+ Deflated Sharpe（DSR）。单段回测好看的 forward-looking / 过拟合
    策略，CPCV 多路径下中位 Sharpe 会塌——这是用来抓过拟合的。

    何时用：
    - **深度 / 稳健性评估**：用户说"稳不稳 / 会不会过拟合 / 深度评估"，或 promote 前把关
    - Swarm grid TopK 候选晋级的二阶段验证

    何时不用：
    - 探索性首轮回测（成本 N×，先用 paper.run_backtest 看方向）
    - 只想看单条收益曲线 → run_backtest

    坑：
    - cpcv 需 bar >= 200；不足**自动回落 walk_forward**（看返回的 splitter_used / note）
    - 看**中位 sharpe_p50** 而非最优 path：挑最好那条 = cherry-pick
    - splitter=cpcv 时 nTestFolds 必须 < nFolds
  `.trim(),
  inputSchema: z
    .object({
      strategyId: z.string().optional().describe("内置策略 ID；与 candidateId 互斥"),
      candidateId: z.string().uuid().optional().describe("候选 UUID；与 strategyId 互斥"),
      params: z.record(z.string(), z.unknown()).default({}).describe("策略参数 dict"),
      venue: z.string().default("binance"),
      symbol: SymbolSchema,
      timeframe: TimeframeSchema.default("1h"),
      fromTs: z.string().datetime().describe("ISO 8601 起始时间"),
      toTs: z.string().datetime().describe("ISO 8601 结束时间"),
      initialCash: z.number().positive().default(10_000),
      feeRate: z.number().min(0).lt(1).default(0.001),
      splitter: z
        .enum(["cpcv", "walk_forward", "purged_kfold"])
        .default("cpcv")
        .describe("时序 CV 切分器；cpcv 最强（多路径）"),
      nFolds: z.number().int().min(2).max(20).default(6).describe("cpcv/kfold 分组数"),
      nTestFolds: z
        .number()
        .int()
        .min(1)
        .default(2)
        .describe("cpcv 每组合取作 test 的组数（须 < nFolds）"),
      embargoPct: z.number().min(0).lt(1).default(0.05),
      wfTestSize: z.number().int().min(1).default(21),
      wfTrainSize: z.number().int().min(1).default(252),
      ...perpInputFields,
    })
    .superRefine((data, ctx) => {
      const hasId = typeof data.strategyId === "string" && data.strategyId.length > 0;
      const hasCand = typeof data.candidateId === "string" && data.candidateId.length > 0;
      if (hasId === hasCand) {
        ctx.addIssue({
          code: "custom",
          message: "必须给 strategyId 或 candidateId，二选一",
          path: ["strategyId"],
        });
      }
      if (data.splitter === "cpcv" && !(data.nTestFolds < data.nFolds)) {
        ctx.addIssue({
          code: "custom",
          message: "cpcv 要求 nTestFolds < nFolds",
          path: ["nTestFolds"],
        });
      }
    }),
  execute: async (inputData, ctx) => {
    const tc = ctx?.requestContext as ToolRequestContext | undefined;
    // CV 跑 N 折 × 多 path（perp 还含逐根 funding），冷缓存下显著慢于单次回测——
    // 给 600s（与 check_sensitivity 一致），300s 极易超时。
    const client = await getBacktestClient(tc, 600_000);
    return await client.cvBacktest({
      strategyId: inputData.strategyId,
      candidateId: inputData.candidateId,
      params: inputData.params ?? {},
      venue: inputData.venue ?? "binance",
      symbol: inputData.symbol,
      timeframe: inputData.timeframe ?? "1h",
      fromTs: inputData.fromTs,
      toTs: inputData.toTs,
      initialCash: inputData.initialCash ?? 10_000,
      feeRate: inputData.feeRate ?? 0.001,
      splitter: inputData.splitter ?? "cpcv",
      nFolds: inputData.nFolds ?? 6,
      nTestFolds: inputData.nTestFolds ?? 2,
      embargoPct: inputData.embargoPct ?? 0.05,
      wfTestSize: inputData.wfTestSize ?? 21,
      wfTrainSize: inputData.wfTrainSize ?? 252,
      tradingMode: inputData.tradingMode,
      leverage: inputData.leverage,
      fundingRate: inputData.fundingRate,
    });
  },
});

// ────────────────────────────────────────────────────────────────────
// D-8c · paper.compose_strategy + paper.list_backtest_runs
// ────────────────────────────────────────────────────────────────────

const StrategyHintSchema = z.object({
  family: z.enum([
    "trend",
    "mean_reversion",
    "buy_hold",
    "breakout",
    "volatility",
    "none",
  ]),
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
    （后端 HTTP 端点本身已放开"无过滤=全局最近 N 条"——那是给控制台活动流用的；
    agent 工具层**有意保留**必填校验:无锚点的全局列表对决策无意义,漏传参数
    应当报错暴露而不是静默拿到一堆不相关的 run。）

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
// D-12 · paper.list_backtest_trades
// ────────────────────────────────────────────────────────────────────

export const paperListBacktestTradesTool = createTool({
  id: "paper.list_backtest_trades",
  description: `
    一次回测的**逐笔成交明细**（按成交先后），含每笔实现盈亏 / 手续费 / intent。

    何时用：
    - 迭代诊断"策略到底亏在哪几笔"——总 metrics 说不出原因时看逐笔：
      是连续小止损磨掉的（手续费/换手问题），还是几笔大亏（止损没生效/逆势扛单）
    - 验证止损/出场逻辑是否真的触发（找 intent=close 的笔看 realized_pnl 分布）
    - 用户问"这次回测都做了哪些交易"

    何时不用：
    - 只要总览指标 → run_backtest 响应里已有（sharpe/win_rate/profit_factor）
    - 看权益曲线形状 → run_backtest 的 equity_curve

    坑：
    - runId 来自 run_backtest 响应的 run_id（或 list_backtest_runs）
    - realized_pnl：开仓笔=0，平仓/反手笔=价差盈亏（**不含手续费**）；
      算净盈亏要自己减 fee
    - 默认 limit=50 控 context；交易多时按需加大（≤500），别无脑拉满
  `.trim(),
  inputSchema: z.object({
    runId: z.string().uuid().describe("回测 run_id（run_backtest 响应 / list_backtest_runs）"),
    limit: z.number().int().min(1).max(500).default(50),
  }),
  execute: async (inputData, ctx) => {
    const tc = ctx?.requestContext as ToolRequestContext | undefined;
    const client = await getClient(tc);
    return await client.listBacktestTrades(inputData.runId, inputData.limit ?? 50);
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
    当前账户快照：现金 / 初始本金 / 持仓估值 / 总权益 / 累计实现 PnL（D-11 多币种）。

    何时用：
    - 用户问"我账户余额 / 我赚了多少 / 我账户总权益"

    返回（D-11）：
    - cash / positions_value / total_equity 均已折算到 base_currency（默认 USD）
    - cash_balances 给出折算前的按币种原始桶（如 {"USD": 5000, "USDT": -1000}）
    - fx_warnings：折算时 FX 不可用 / 偏旧的币种告警

    坑：
    - 持仓估值用 avg_open_price 兜底（D-8b 不接实时 mark）；实际权益略偏保守
    - **fx_warnings 非空时必须原样转告用户**——表示某些币种 FX 拿不到被排除出估值，
      或汇率偏旧，总权益可能不完整（金融时效硬约束）
    - 默认初始 10000，首次下单时 lazy create
  `.trim(),
  inputSchema: z.object({}),
  execute: async (_input, ctx) => {
    const tc = ctx?.requestContext as ToolRequestContext | undefined;
    const client = await getClient(tc);
    return await client.getAccount();
  },
});

// ────────────────────────────────────────────────────────────────────
// D-11 · live runner（issue #1）
// ────────────────────────────────────────────────────────────────────

export const paperStartStrategyTool = createTool({
  id: "paper.start_strategy",
  description: `
    把一个 **已 promoted** 的策略候选放到模拟盘按行情自动跑（live runner）。
    起一个后台 runner：按 timeframe 拉最新 K 线喂策略 on_bar，下单意图走护栏内
    plan/exec 链路落账（过风控 + 审计），positions / 总权益随行情自动更新。

    何时用：
    - 用户明确说"把这个策略放到模拟盘 / 让它自动跑 / 实时跟行情"
    - **前提**：候选必须先 promote（paper.promote_candidate）；本工具只接受 promoted

    何时不用：
    - 只想跑一次历史回测 → paper.run_backtest
    - 候选还没 promote → 先让用户确认 promote

    坑：
    - **promote ≠ 自动跑**：promote 只是状态切换，必须再调本工具才真正按行情跑
    - 同一个 candidate 同时只能有一个 running（再起会 409）；先 stop 再换 symbol
    - candidate 表不含 venue/symbol/timeframe，必须在这里指定
    - 机器自动审批下单（approved_by=system:live_runner），正当性靠"人先 promote + 人显式 start"
  `.trim(),
  inputSchema: z.object({
    candidateId: z.string().uuid().describe("已 promoted 的候选 id"),
    venue: z
      .string()
      .describe(
        "数据源 venue，**必填**：按 symbol 的市场分类推导，不要留空 / 默认 binance。" +
        "crypto→binance；美股 / 全球指数→yfinance(或 alpaca)；A股 sh./sz. + 港股 hk.→akshare。" +
        "venue 与 symbol 市场不符会喂错行情、策略空跑或报错。",
      ),
    symbol: SymbolSchema,
    timeframe: TimeframeSchema.default("1h"),
    params: z.record(z.string(), z.unknown()).optional().describe("策略参数（缺省用策略默认值）"),
    tradingMode: z
      .enum(["spot", "perp"])
      .default("spot")
      .describe(
        "spot（默认，现货 long-only）或 perp（USDT-M 永续 + 逐仓，放开做空 / 杠杆）。" +
        "perp **仅** crypto 永续标的（ccxt 记法 BTC/USDT:USDT，非现货 BTC/USDT），" +
        "非 crypto / 非永续会被服务端 422 拒。perp 须配**含做空逻辑**的策略，long-only 策略会告警。",
      ),
    leverage: z
      .number()
      .int()
      .min(1)
      .max(20)
      .default(1)
      .describe("杠杆倍数（perp 用，1..20）；spot 恒 1"),
  }),
  execute: async (inputData, ctx) => {
    const tc = ctx?.requestContext as ToolRequestContext | undefined;
    const client = await getClient(tc);
    return await client.startStrategy({
      candidateId: inputData.candidateId,
      venue: inputData.venue,
      symbol: inputData.symbol,
      timeframe: inputData.timeframe,
      params: inputData.params,
      tradingMode: inputData.tradingMode,
      leverage: inputData.leverage,
    });
  },
});

export const paperStopStrategyTool = createTool({
  id: "paper.stop_strategy",
  description: `
    停掉一个正在模拟盘跑的 live runner（按 run id）。停后该 candidate 可重新 start。

    何时用：用户说"停掉那个策略 / 别让它跑了"
    何时不用：想看跑得怎么样 → paper.list_strategy_runs
  `.trim(),
  inputSchema: z.object({
    runId: z.string().uuid().describe("strategy_run id（来自 start_strategy / list_strategy_runs）"),
  }),
  execute: async (inputData, ctx) => {
    const tc = ctx?.requestContext as ToolRequestContext | undefined;
    const client = await getClient(tc);
    return await client.stopStrategy(inputData.runId);
  },
});

export const paperListStrategyRunsTool = createTool({
  id: "paper.list_strategy_runs",
  description: `
    列出当前账户的 live runner：状态（running/stopped/errored）/ 累计 pnl /
    已处理到的最新 bar / 错误日志。

    何时用：用户问"我有哪些策略在跑 / 跑得怎么样 / 那个策略赚了多少"
    坑：cumulative_pnl 是 mark-to-market 估算；errored 状态看 error_log 找原因
  `.trim(),
  inputSchema: z.object({
    status: z.enum(["running", "stopped", "errored"]).optional().describe("按状态过滤"),
  }),
  execute: async (inputData, ctx) => {
    const tc = ctx?.requestContext as ToolRequestContext | undefined;
    const client = await getClient(tc);
    return await client.listStrategyRuns({ status: inputData.status });
  },
});

export const paperListStrategyRunDecisionsTool = createTool({
  id: "paper.list_strategy_run_decisions",
  description: `
    一个 live run 的**决策复盘时间线**：每根 bar 策略产生的下单意图 + 撮合结果。

    何时用：
    - 用户问"那个策略都做了哪些决定 / 为什么买在这里 / 复盘一下它的操作"

    返回每行：bar 时点 / 价、side / quantity / 类型 / tag(策略语义意图)、
    outcome（filled / rejected / risk_rejected）、成交价、plan_id / order_id（可交叉
    查 trade_plans 的 rationale 与 closed_trades 的盈亏）、reason。

    坑：
    - 只记**产生了下单意图**的 bar（决策事件流，非逐 bar 全量快照）
    - 确定性策略"代码即理由"，细粒度信号上下文看 tag / 结合 candidate 源码
  `.trim(),
  inputSchema: z.object({
    runId: z.string().uuid().describe("strategy_run id"),
    // 默认 50（复盘通常看最近几十条决策就够；200 条 ≈ 数万 token 进消息历史）
    limit: z.number().int().min(1).max(500).default(50),
  }),
  execute: async (inputData, ctx) => {
    const tc = ctx?.requestContext as ToolRequestContext | undefined;
    const client = await getClient(tc);
    return await client.listStrategyRunDecisions(inputData.runId, inputData.limit);
  },
});

export const paperTools = [
  paperListStrategiesTool,
  paperRunBacktestTool,
  paperCheckSensitivityTool,
  paperCvBacktestTool,
  paperHealthTool,
  paperListOrdersTool,
  paperListPositionsTool,
  paperGetAccountTool,
  paperComposeStrategyTool,
  paperListBacktestRunsTool,
  paperListBacktestTradesTool,
  paperStartStrategyTool,
  paperStopStrategyTool,
  paperListStrategyRunsTool,
  paperListStrategyRunDecisionsTool,
] as const;
