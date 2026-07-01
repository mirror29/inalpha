/**
 * 共享领域 Schema —— 量化数据类型定义。
 *
 * 用 Zod 替代手写 type predicate（shape guard），让 tool 输出格式
 * 有单一事实来源。前端 tool-view 和后端 tool 定义都从这引用。
 *
 * 当前 P1 阶段：先覆盖最高频的行情/回测/因子类型。
 * 未来每个 tool 的 output 都应有对应的 Zod schema。
 */
import { z } from "zod";

// ── 行情 ────────────────────────────────────────────────────────────

export const BarSchema = z.object({
  ts: z.string(),
  open: z.number(),
  high: z.number(),
  low: z.number(),
  close: z.number(),
  volume: z.number(),
});

export type Bar = z.infer<typeof BarSchema>;

export const BarsResultSchema = z.object({
  venue: z.string(),
  symbol: z.string(),
  timeframe: z.string(),
  bars: z.array(BarSchema),
});

export type BarsResult = z.infer<typeof BarsResultSchema>;

export const TickerSchema = z.object({
  venue: z.string(),
  symbol: z.string(),
  price: z.number(),
  ts: z.string().optional(),
  source: z.string().optional(),
  is_stale: z.boolean().optional(),
  stale_seconds: z.number().optional(),
});

export type Ticker = z.infer<typeof TickerSchema>;

// ── 回测 ────────────────────────────────────────────────────────────

export const BacktestMetricsSchema = z.object({
  sharpe: z.number().optional(),
  sortino: z.number().optional(),
  max_drawdown_pct: z.number().optional(),
  calmar: z.number().optional(),
  win_rate_pct: z.number().optional(),
  total_trades: z.number().optional(),
  profit_factor: z.number().optional(),
});

export const BacktestResultSchema = z.object({
  run_id: z.string().optional(),
  fitness: z.number().optional(),
  sharpe: z.number().optional(),
  max_drawdown_pct: z.number().optional(),
  blew_up: z.boolean().optional(),
  health_warnings: z.unknown().optional(),
  final_equity: z.number().optional(),
  num_trades: z.number().optional(),
  metrics: BacktestMetricsSchema.optional(),
  baseline: z
    .object({
      fitness: z.number().optional(),
      sharpe: z.number().optional(),
      max_drawdown_pct: z.number().optional(),
      blew_up: z.boolean().optional(),
    })
    .optional(),
  validation: z
    .object({
      decay_ratio: z.number().optional(),
      holdout: z.object({ sharpe: z.number().optional() }).optional(),
    })
    .optional(),
  equity_curve: z.array(z.tuple([z.string(), z.number()]).or(z.unknown())).optional(),
  trades: z
    .array(
      z.object({
        ts: z.string().optional(),
        side: z.string().optional(),
        price: z.number().optional(),
        quantity: z.number().optional(),
        pnl: z.number().optional(),
      }),
    )
    .optional(),
});

export type BacktestResult = z.infer<typeof BacktestResultSchema>;

// ── 因子 ────────────────────────────────────────────────────────────

export const FactorScoreSchema = z.object({
  name: z.string(),
  kind: z.string().optional(),
  rank_ic: z.number().optional(),
  rank_ic_recent: z.number().optional(),
  direction: z.number().optional(),
  decay_state: z.enum(["stable", "fading", "decaying"]).optional(),
  ic_null_benchmark: z.number().optional(),
  strength: z.number().optional(),
  reading: z.number().optional(),
});

export type FactorScore = z.infer<typeof FactorScoreSchema>;

export const FactorScoreResultSchema = z.object({
  symbol: z.string(),
  timeframe: z.string(),
  top: z.array(FactorScoreSchema).optional(),
  available: z.boolean().optional(),
  scored_at: z.string().optional(),
});

export type FactorScoreResult = z.infer<typeof FactorScoreResultSchema>;

// ── 基本面 ──────────────────────────────────────────────────────────

export const FundamentalsSchema = z.object({
  symbol: z.string().optional(),
  venue: z.string().optional(),
  indicators: z.record(z.unknown()).optional(),
  categories: z.record(z.array(z.string())).optional(),
});

export type Fundamentals = z.infer<typeof FundamentalsSchema>;

// ── 工具函数 ────────────────────────────────────────────────────────

/** Zod schema 校验结果：ok=true 时 value 类型安全。 */
export type Validated<T> =
  | { ok: true; value: T }
  | { ok: false; error: string };

/**
 * 用 Zod schema 校验未知 shape，替代手写 type predicate。
 *
 * 用法：
 *   const r = validateShape(BarsResultSchema, data);
 *   if (r.ok) { r.value.bars[0].close }  // 类型安全
 *
 * 比手写 `isBars(v): v is BarsShape` 的优势：
 * - schema 与类型定义在同一处，改一处即改全部
 * - 错误信息精确（哪个字段类型不对），方便 debug
 * - 后端和前端可以共享同一份 schema
 */
export function validateShape<T>(
  schema: z.ZodType<T>,
  data: unknown,
): Validated<T> {
  const result = schema.safeParse(data);
  if (result.success) return { ok: true, value: result.data };
  return { ok: false, error: result.error.issues.map(
    (i) => `${i.path.join(".")}: ${i.message}`,
  ).join("; ") };
}
