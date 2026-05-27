/**
 * Backtest grid workflow —— Swarm S1 真实实现（ADR-0025 §D3）。
 *
 * 形状：
 *
 *   expand →  foreach(runOne, { concurrency: 4 })  →  aggregate
 *   笛卡尔  扇出到 N 个 HTTP，并发 4 个 in-flight    Pareto 前沿 + topK
 *
 * 关键约束：
 *
 * - **strategies × symbols ≤ 20**：grid-size-cap PreToolUse hook + workflow schema 双层保
 *   （max 上限改 input schema 即可，不动 step）
 * - **单 job 失败不阻断**：runOne 内 try/catch 翻成 ``{ error, report:null }``，aggregate
 *   只看 ok 的 reports 算 Pareto
 * - **dedupe**：expand 把 ``strategy|symbol|timeframe|from|to`` 同 key 的合并，避免 LLM
 *   传重复触发 paper-service 重算
 * - **concurrency = 4**：和 paper ProcessPool 的 max_workers 解耦；这里控 in-flight HTTP，
 *   服务端 pool 控真实 CPU 并发
 *
 * 单 backtest 调走 PaperClient.runBacktest → ``POST /backtest``（pool 已起则进 ProcessPool）。
 */
import { createStep, createWorkflow } from "@mastra/core/workflows";
import { z } from "zod";

import { mintServiceToken } from "../../auth.js";
import {
  BacktestReport as PaperBacktestReport,
  PaperClient,
} from "../../clients/paper.js";
import { getSettings } from "../../config.js";

// ─── schemas ───────────────────────────────────────────────────────

const StrategyIdSchema = z.enum(["sma_cross", "buy_and_hold", "mean_reversion"]);
const TimeframeSchema = z.enum(["1m", "5m", "15m", "1h", "4h", "1d"]);
// D-9 multi-market：与 tools/paper.ts 保持一致——5 venue × 5 资产全覆盖。
// crypto 'BTC/USDT' / 美股 'AAPL' / 指数 '^N225' / akshare 'sh.600519' /
// yfinance '005930.KS' / FRED 'DFF' 都应通过。
const SymbolSchema = z
  .string()
  .min(1)
  .max(50)
  .regex(
    /^[\^A-Za-z0-9._/-]+$/,
    "symbol 不能为空 / 含空格；支持 crypto 'BTC/USDT' / 普通 'AAPL' / 指数 '^N225' / akshare 'sh.600519' / yfinance '005930.KS' / FRED 'DFF'",
  );

// **Note**：strategies + candidateIds "至少一个非空 / 总数 ≤ 5" 的校验放在
// expandStep 内部抛错，不放 .superRefine —— mastra 1.36 + zod 4 的 standard-schema
// adapter 对 .superRefine 处理有缺陷，会把合法 input 误判（dedupes 测试踩过）。
// 单端 max(5) 仍由 zod 校验；跨端总数 + 互斥由 step 校验，workflow status='failed' 触达。
const GridInputSchema = z.object({
  /** 内置 baseline 策略（与 candidateIds 至少一个非空） */
  strategies: z.array(StrategyIdSchema).max(5).optional(),
  /**
   * D-9 candidate 路径：LLM 自创策略候选 ID 列表（来自 paper.author_strategy）。
   * 与 strategies 共同组成笛卡尔积 = (strategies ∪ candidateIds) × symbols。
   */
  candidateIds: z.array(z.string().uuid()).max(5).optional(),
  symbols: z.array(SymbolSchema).min(1).max(8),
  venue: z.string().default("binance"),
  timeframe: TimeframeSchema.default("1h"),
  // **D-9 fix**：optional + 服务端默认近 30 天。LLM 训练 cutoff 早，不该让它
  // 自己猜 "现在是哪天"——省略时 expand step 用 now / now-30d 兜底。
  from_ts: z.iso.datetime().optional(),
  to_ts: z.iso.datetime().optional(),
  initial_cash: z.number().positive().default(10_000),
  fee_rate: z.number().min(0).lt(1).default(0.001),
});

const JobSchema = z
  .object({
    /** 内置策略 ID；走 candidate 路径时为 null */
    strategy_id: StrategyIdSchema.nullable(),
    /** D-9 candidate 路径：候选 UUID；走内置时为 null */
    candidate_id: z.string().uuid().nullable(),
    symbol: SymbolSchema,
    venue: z.string(),
    timeframe: TimeframeSchema,
    from_ts: z.string(),
    to_ts: z.string(),
    initial_cash: z.number(),
    fee_rate: z.number(),
  })
  .refine(
    (j) => (j.strategy_id === null) !== (j.candidate_id === null),
    "Job must have exactly one of strategy_id / candidate_id",
  );
type Job = z.infer<typeof JobSchema>;

/** D-9 baseline 对照（candidate 路径 PaperBacktestReport.baseline 透传）。 */
const BaselineShimSchema = z
  .object({
    strategy_id: z.string(),
    fitness: z.number().nullish(),
    sharpe: z.number().nullish(),
    max_drawdown_pct: z.number(),
    total_return_pct: z.number(),
    num_trades: z.number().int(),
  })
  .nullish();

const ReportShimSchema = z.object({
  /** 内置 ID 或 'candidate:<uuid>' 字面（agent 区分用） */
  strategy_id: z.string(),
  /** D-9：candidate 路径回填 UUID；内置路径为 null / undefined */
  candidate_id: z.string().uuid().nullish(),
  symbol: z.string(),
  sharpe: z.number().nullish(),
  max_drawdown_pct: z.number(),
  total_return_pct: z.number(),
  final_equity: z.number(),
  num_trades: z.number().int(),
  /** D-9 多目标 fitness（排序候选用）；内置路径为 null / undefined */
  fitness: z.number().nullish(),
  /** D-9 candidate 路径自动并跑的 buy_and_hold 对照；内置路径为 null / undefined */
  baseline: BaselineShimSchema,
});
type ReportShim = z.infer<typeof ReportShimSchema>;

const RunResultSchema = z.object({
  job: JobSchema,
  report: ReportShimSchema.nullable(),
  error: z
    .object({
      code: z.string(),
      message: z.string(),
    })
    .nullable(),
});

const ParetoPointSchema = z.object({
  strategy_id: z.string(),
  symbol: z.string(),
  sharpe: z.number().nullable(),
  max_drawdown_pct: z.number(),
  total_return_pct: z.number(),
});

const GridOutputSchema = z.object({
  reports: z.array(RunResultSchema),
  pareto: z.array(ParetoPointSchema),
  top_k: z.array(ParetoPointSchema),
  summary: z.object({
    total: z.number().int(),
    ok: z.number().int(),
    errored: z.number().int(),
    wall_time_ms: z.number().int(),
  }),
});

// ─── steps ─────────────────────────────────────────────────────────

const expandStep = createStep({
  id: "expand",
  inputSchema: GridInputSchema,
  outputSchema: z.array(JobSchema),
  execute: async ({ inputData }) => {
    // **D-9 fix**：缺 from_ts / to_ts 时用 now / now-30d 兜底
    const now = new Date();
    const toTs = inputData.to_ts ?? now.toISOString();
    const fromTs = inputData.from_ts ?? new Date(now.getTime() - 30 * 86_400_000).toISOString();

    // 笛卡尔积 + dedupe（同 source(strategy_id|candidate_id)+symbol+window 合并）
    // D-9：strategies ∪ candidateIds 两条来源都扇出
    const seen = new Set<string>();
    const out: Job[] = [];

    const strategies = inputData.strategies ?? [];
    const candidateIds = inputData.candidateIds ?? [];

    // 跨端校验（superRefine 在 mastra 1.36 + zod 4 下不稳定，放这里抛）
    const total = strategies.length + candidateIds.length;
    if (total === 0) {
      throw new Error(
        "grid input invalid: must provide at least one of strategies / candidateIds",
      );
    }
    if (total > 5) {
      throw new Error(
        `grid input invalid: strategies + candidateIds total ${total} exceeds 5 ` +
          `(per-side cap; symbols × total also subject to grid-size-cap 20)`,
      );
    }

    // 1. 内置 strategies 笛卡尔
    for (const s of strategies) {
      for (const sym of inputData.symbols) {
        const key = `s:${s}|${sym}|${inputData.timeframe}|${fromTs}|${toTs}`;
        if (seen.has(key)) continue;
        seen.add(key);
        out.push({
          strategy_id: s,
          candidate_id: null,
          symbol: sym,
          venue: inputData.venue,
          timeframe: inputData.timeframe,
          from_ts: fromTs,
          to_ts: toTs,
          initial_cash: inputData.initial_cash,
          fee_rate: inputData.fee_rate,
        });
      }
    }

    // 2. D-9 candidate 笛卡尔
    for (const cid of candidateIds) {
      for (const sym of inputData.symbols) {
        const key = `c:${cid}|${sym}|${inputData.timeframe}|${fromTs}|${toTs}`;
        if (seen.has(key)) continue;
        seen.add(key);
        out.push({
          strategy_id: null,
          candidate_id: cid,
          symbol: sym,
          venue: inputData.venue,
          timeframe: inputData.timeframe,
          from_ts: fromTs,
          to_ts: toTs,
          initial_cash: inputData.initial_cash,
          fee_rate: inputData.fee_rate,
        });
      }
    }

    return out;
  },
});

// foreach 真正的并发执行点：concurrency 个 in-flight HTTP 同时打 paper
const runOneStep = createStep({
  id: "run_one",
  inputSchema: JobSchema,
  outputSchema: RunResultSchema,
  execute: async ({ inputData }) => {
    const settings = getSettings();
    const token = await mintServiceToken({ sub: "service:swarm" });
    const client = new PaperClient({ baseUrl: settings.paperServiceUrl, token });

    try {
      // D-9：strategy_id / candidate_id 二选一（refine 已保证），分支调用
      const report: PaperBacktestReport = await client.runBacktest({
        strategyId: inputData.strategy_id ?? undefined,
        candidateId: inputData.candidate_id ?? undefined,
        symbol: inputData.symbol,
        venue: inputData.venue,
        timeframe: inputData.timeframe,
        fromTs: inputData.from_ts,
        toTs: inputData.to_ts,
        initialCash: inputData.initial_cash,
        feeRate: inputData.fee_rate,
      });
      const shim: ReportShim = {
        // report.strategy_id 已是 'candidate:<uuid>' 或内置 ID 字符串
        strategy_id: report.strategy_id,
        candidate_id: report.candidate_id,
        symbol: inputData.symbol,
        sharpe: report.sharpe,
        max_drawdown_pct: report.max_drawdown_pct,
        total_return_pct: report.total_return_pct,
        final_equity: report.final_equity,
        num_trades: report.num_trades,
        fitness: report.fitness,
        baseline: report.baseline,
      };
      return { job: inputData, report: shim, error: null };
    } catch (e: unknown) {
      const code =
        e && typeof e === "object" && "code" in e ? String((e as { code: unknown }).code) : "UNKNOWN";
      const message = e instanceof Error ? e.message : String(e);
      return { job: inputData, report: null, error: { code, message } };
    }
  },
});

const aggregateStep = createStep({
  id: "aggregate",
  inputSchema: z.array(RunResultSchema),
  outputSchema: GridOutputSchema,
  execute: async ({ inputData }) => {
    const t0 = Date.now();
    const ok = inputData.filter((r) => r.report !== null && r.error === null);
    const errored = inputData.length - ok.length;

    // 转 Pareto 计算输入（sharpe nullish → 统一到 null）
    const points: ParetoPoint[] = ok.map((r) => ({
      strategy_id: r.report!.strategy_id,
      symbol: r.report!.symbol,
      sharpe: r.report!.sharpe ?? null,
      max_drawdown_pct: r.report!.max_drawdown_pct,
      total_return_pct: r.report!.total_return_pct,
    }));

    const pareto = computeParetoFrontier(points);
    const top_k = pickTopK(points, 3);

    return {
      reports: inputData,
      pareto,
      top_k,
      summary: {
        total: inputData.length,
        ok: ok.length,
        errored,
        wall_time_ms: Date.now() - t0,
      },
    };
  },
});

// ─── 纯函数：Pareto 前沿 + topK ─────────────────────────────────────

type ParetoPoint = z.infer<typeof ParetoPointSchema>;

/**
 * 目标二维：**最大化 sharpe**、**最小化 max_drawdown_pct**。
 *
 * 算法：O(n²) brute force —— 一个点被另一个点严格 dominate（sharpe 更高 AND
 * max_dd 更低）则剔除，否则进 Pareto 前沿。sharpe=null 直接出局（视为最差）。
 *
 * grid ≤ 20 量级，O(n²) 没问题；上百再换成排序+sweep。
 */
export function computeParetoFrontier(points: ParetoPoint[]): ParetoPoint[] {
  const valid = points.filter((p) => p.sharpe !== null);
  const out: ParetoPoint[] = [];
  for (const p of valid) {
    let dominated = false;
    for (const q of valid) {
      if (q === p) continue;
      if (
        q.sharpe !== null &&
        p.sharpe !== null &&
        q.sharpe >= p.sharpe &&
        q.max_drawdown_pct <= p.max_drawdown_pct &&
        (q.sharpe > p.sharpe || q.max_drawdown_pct < p.max_drawdown_pct)
      ) {
        dominated = true;
        break;
      }
    }
    if (!dominated) out.push(p);
  }
  return out;
}

/** 按 sharpe 倒序取 top K（null sharpe 排尾）。 */
export function pickTopK(points: ParetoPoint[], k: number): ParetoPoint[] {
  return [...points]
    .sort((a, b) => {
      if (a.sharpe === null && b.sharpe === null) return 0;
      if (a.sharpe === null) return 1;
      if (b.sharpe === null) return -1;
      return b.sharpe - a.sharpe;
    })
    .slice(0, k);
}

// ─── workflow ──────────────────────────────────────────────────────

export const backtestGridWorkflow = createWorkflow({
  id: "backtest_grid",
  inputSchema: GridInputSchema,
  outputSchema: GridOutputSchema,
})
  .then(expandStep)
  .foreach(runOneStep, { concurrency: 4 })
  .then(aggregateStep)
  .commit();

// 测试用：导出 schemas / 纯函数
export {
  GridInputSchema,
  GridOutputSchema,
  JobSchema,
  ParetoPointSchema,
  RunResultSchema,
};
