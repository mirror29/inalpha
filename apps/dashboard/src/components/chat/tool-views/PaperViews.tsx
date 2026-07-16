"use client";

import { cn } from "@/lib/cn";

import {
  fmtNum,
  fmtSigned,
  shortDate,
  shortId,
  shortTimestamp,
} from "./format";
import {
  CollapseSection,
  MetricGrid,
  Pnl,
  Sparkline,
  StatusBadge,
  SymbolHeader,
} from "./primitives";

/**
 * paper 域工具视图:策略候选 / 模拟盘运行 / 回测 / 账户持仓。
 * 每个视图带 shape guard,形态不符回 null 由通用 ToolOutput 兜底。
 */

// ── 候选策略 ──────────────────────────────────────────────────────

export interface CandidateShape {
  id: string;
  description: string;
  status: string;
  author?: string;
  fitness?: number | null;
  metrics?: Record<string, unknown> | null;
  code?: string;
  created_at?: string;
}

export function isCandidate(v: unknown): v is CandidateShape {
  const o = v as CandidateShape;
  return (
    !!o &&
    typeof o === "object" &&
    typeof o.description === "string" &&
    typeof o.status === "string" &&
    typeof o.id === "string"
  );
}

export function isCandidateList(v: unknown): v is CandidateShape[] {
  return Array.isArray(v) && v.length > 0 && v.every(isCandidate);
}

/** metrics dict 里挑常用绩效键做指标格(命名以 paper 服务为准)。 */
function candidateMetrics(m: Record<string, unknown> | null | undefined) {
  if (!m) return [];
  const pick: [string, string][] = [
    ["sharpe", "sharpe"],
    ["sortino", "sortino"],
    ["calmar", "calmar"],
    ["max_drawdown_pct", "max dd %"],
    ["total_return_pct", "return %"],
    ["win_rate", "win %"],
    ["num_trades", "trades"],
  ];
  return pick
    .filter(([k]) => typeof m[k] === "number")
    .map(([k, label]) => ({ label, value: fmtNum(m[k] as number) }));
}

/** 单个候选卡片:状态 + 描述 + fitness/绩效格 + 代码折叠。 */
export function CandidateView({ c }: { c: CandidateShape }) {
  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex items-center gap-2">
        <StatusBadge status={c.status} />
        {c.fitness != null && (
          <span className="font-mono text-[10px] tabular-nums text-fg-muted">
            fitness {fmtNum(c.fitness)}
          </span>
        )}
        <span className="ml-auto font-mono text-[10px] text-fg-muted/50">
          {shortId(c.id)}
          {c.author ? ` · ${c.author}` : ""}
        </span>
      </div>
      <p className="text-[11px] leading-relaxed text-fg">{c.description}</p>
      <MetricGrid items={candidateMetrics(c.metrics)} />
      {c.code && (
        <CollapseSection label="code" hint={`${c.code.split("\n").length} lines`}>
          <pre className="max-h-48 overflow-auto whitespace-pre-wrap break-all font-mono text-[10px] leading-relaxed text-fg-muted">
            {c.code}
          </pre>
        </CollapseSection>
      )}
    </div>
  );
}

/** 候选列表:紧凑行(状态 + 描述截断 + fitness)。 */
export function CandidateListView({ list }: { list: CandidateShape[] }) {
  return (
    <div className="flex flex-col gap-1">
      {list.map((c) => (
        <div
          key={c.id}
          className="flex items-start gap-2 rounded-sm border border-border-subtle/60 bg-bg/40 px-1.5 py-1"
        >
          <StatusBadge status={c.status} />
          <span className="min-w-0 flex-1 truncate text-[11px] text-fg" title={c.description}>
            {c.description}
          </span>
          {c.fitness != null && (
            <span className="shrink-0 font-mono text-[10px] tabular-nums text-fg-muted">
              {fmtNum(c.fitness)}
            </span>
          )}
        </div>
      ))}
    </div>
  );
}

// ── 模拟盘运行(strategy run)──────────────────────────────────────

export interface StrategyRunShape {
  id: string;
  status: string;
  venue: string;
  symbol: string;
  timeframe: string;
  cumulative_pnl?: number;
  last_bar_ts?: string | null;
  started_at?: string;
  stopped_at?: string | null;
  run_log?: { ts?: string; level?: string; msg?: string }[];
}

export function isStrategyRun(v: unknown): v is StrategyRunShape {
  const o = v as StrategyRunShape;
  return (
    !!o &&
    typeof o === "object" &&
    typeof o.status === "string" &&
    typeof o.symbol === "string" &&
    typeof o.timeframe === "string" &&
    ("cumulative_pnl" in o || "run_log" in o || "last_bar_ts" in o)
  );
}

export function isStrategyRunList(v: unknown): v is StrategyRunShape[] {
  return Array.isArray(v) && v.length > 0 && v.every(isStrategyRun);
}

const LOG_TONE: Record<string, string> = {
  error: "text-fox-red",
  warn: "text-gold",
};

/** 单个 run 卡片:状态 + 标的 + 累计盈亏 + 最近 bar + 日志折叠。 */
export function StrategyRunView({ r }: { r: StrategyRunShape }) {
  const log = r.run_log ?? [];
  return (
    <div className="flex flex-col gap-1.5">
      <SymbolHeader
        symbol={r.symbol}
        tags={[r.venue, r.timeframe]}
        right={<StatusBadge status={r.status} />}
      />
      <div className="flex items-baseline gap-3 font-mono text-[11px]">
        {r.cumulative_pnl != null && (
          <span>
            pnl <Pnl value={r.cumulative_pnl} />
          </span>
        )}
        <span className="text-fg-muted/60">
          {r.last_bar_ts
            ? `last bar ${shortTimestamp(r.last_bar_ts) ?? r.last_bar_ts}`
            : null}
        </span>
        <span className="ml-auto text-[10px] text-fg-muted/50">{shortId(r.id)}</span>
      </div>
      {log.length > 0 && (
        <CollapseSection label="run log" hint={`${log.length}`}>
          <div className="max-h-40 overflow-auto font-mono text-[10px] leading-relaxed">
            {log.slice(-50).map((l, i) => (
              <div key={i} className="flex gap-1.5">
                <span className="shrink-0 text-fg-muted/40">
                  {l.ts ? (shortTimestamp(l.ts)?.slice(5) ?? "") : ""}
                </span>
                <span
                  className={cn(
                    "min-w-0 break-words",
                    LOG_TONE[l.level ?? ""] ?? "text-fg-muted",
                  )}
                >
                  {l.msg ?? ""}
                </span>
              </div>
            ))}
          </div>
        </CollapseSection>
      )}
    </div>
  );
}

/** run 列表:紧凑行。 */
export function StrategyRunListView({ list }: { list: StrategyRunShape[] }) {
  return (
    <div className="flex flex-col gap-1">
      {list.map((r) => (
        <div
          key={r.id}
          className="flex items-center gap-2 rounded-sm border border-border-subtle/60 bg-bg/40 px-1.5 py-1 font-mono text-[11px]"
        >
          <StatusBadge status={r.status} />
          <span className="text-fg">{r.symbol}</span>
          <span className="text-[10px] text-fg-muted/60">
            {r.venue} · {r.timeframe}
          </span>
          {r.cumulative_pnl != null && (
            <span className="ml-auto">
              <Pnl value={r.cumulative_pnl} />
            </span>
          )}
        </div>
      ))}
    </div>
  );
}

// ── 回测 ──────────────────────────────────────────────────────────

export interface BacktestShape {
  strategy_id?: string;
  symbol?: string;
  venue?: string;
  timeframe?: string;
  total_return_pct?: number;
  sharpe?: number | null;
  sortino?: number | null;
  max_drawdown_pct?: number;
  win_rate?: number | null;
  num_trades?: number;
  fitness?: number | null;
  total_fees?: number;
  num_bars_processed?: number;
  final_equity?: number;
  initial_cash?: number;
  period_start?: string;
  period_end?: string;
  equity_curve?: { ts: string; equity: number }[];
  baseline?: {
    strategy_id?: string;
    fitness?: number | null;
    sharpe?: number | null;
    total_return_pct?: number;
  } | null;
  blew_up?: boolean;
  health_warnings?: string[];
}

export function isBacktest(v: unknown): v is BacktestShape {
  const o = v as BacktestShape;
  return (
    !!o &&
    typeof o === "object" &&
    typeof o.total_return_pct === "number" &&
    ("num_trades" in o || "equity_curve" in o || "max_drawdown_pct" in o)
  );
}

/** 回测结果:穿仓告警 → 指标格 → 权益曲线 → baseline 对照。 */
export function BacktestView({ b }: { b: BacktestShape }) {
  const warnings = [
    ...(b.blew_up ? ["blew up — 账户穿仓,本次回测物理不可信"] : []),
    ...(b.health_warnings ?? []),
  ];
  // 9 项 = 3 列网格整三排(sharpe/sortino/win 可能为 null 被滤掉,fees/bars 总有值兜满)。
  const metrics: [string, string | null][] = [
    ["return %", b.total_return_pct != null ? fmtSigned(b.total_return_pct) : null],
    ["sharpe", b.sharpe != null ? fmtNum(b.sharpe) : null],
    ["sortino", b.sortino != null ? fmtNum(b.sortino) : null],
    ["max dd %", b.max_drawdown_pct != null ? fmtNum(b.max_drawdown_pct) : null],
    ["win %", b.win_rate != null ? fmtNum(b.win_rate) : null],
    ["trades", b.num_trades != null ? String(b.num_trades) : null],
    ["fees", b.total_fees != null ? fmtNum(b.total_fees) : null],
    ["bars", b.num_bars_processed != null ? String(b.num_bars_processed) : null],
    ["fitness", b.fitness != null ? fmtNum(b.fitness) : null],
  ];
  const metricItems = metrics
    .filter((x): x is [string, string] => x[1] != null)
    .map(([label, value]) => ({ label, value }));

  return (
    <div className="flex flex-col gap-1.5">
      <SymbolHeader
        symbol={b.symbol ?? b.strategy_id ?? ""}
        tags={[b.venue, b.timeframe, b.strategy_id !== b.symbol ? b.strategy_id : null]}
      />
      {warnings.map((w) => (
        <div
          key={w}
          className="rounded-sm border border-fox-red/40 bg-fox-red/10 px-1.5 py-1 text-[10px] text-fox-red"
        >
          {w}
        </div>
      ))}
      <MetricGrid items={metricItems} />
      {!!b.equity_curve?.length && (
        <Sparkline values={b.equity_curve.map((p) => p.equity)} />
      )}
      <div className="flex flex-wrap gap-x-3 font-mono text-[10px] text-fg-muted/60">
        {b.period_start && b.period_end && (
          <span>
            {shortDate(b.period_start)} → {shortDate(b.period_end)}
          </span>
        )}
        {b.initial_cash != null && b.final_equity != null && (
          <span>
            {fmtNum(b.initial_cash)} → {fmtNum(b.final_equity)}
          </span>
        )}
      </div>
      {b.baseline && (
        <div className="flex flex-wrap items-baseline gap-x-2 rounded-sm border border-border-subtle/60 bg-bg/40 px-1.5 py-1 font-mono text-[10px] text-fg-muted">
          <span className="uppercase tracking-wider text-fg-muted/50">
            baseline · {b.baseline.strategy_id ?? "buy_and_hold"}
          </span>
          {b.baseline.total_return_pct != null && (
            <span>return {fmtSigned(b.baseline.total_return_pct)}%</span>
          )}
          {b.baseline.sharpe != null && <span>sharpe {fmtNum(b.baseline.sharpe)}</span>}
          {b.baseline.fitness != null && (
            <span>fitness {fmtNum(b.baseline.fitness)}</span>
          )}
        </div>
      )}
    </div>
  );
}

// ── 账户 / 持仓 ───────────────────────────────────────────────────

export interface AccountShape {
  account_id: string;
  base_currency?: string;
  cash: number;
  initial_cash?: number;
  positions_value?: number;
  total_equity?: number;
  net_external_flows?: number;
  cash_balances?: Record<string, number>;
  perp_margin_locked?: number;
  fx_warnings?: string[];
}

export function isAccount(v: unknown): v is AccountShape {
  const o = v as AccountShape;
  return (
    !!o &&
    typeof o === "object" &&
    typeof o.account_id === "string" &&
    typeof o.cash === "number"
  );
}

/** 账户快照:总权益 + 现金/持仓 + 币种桶 + FX 告警。 */
export function AccountView({ a }: { a: AccountShape }) {
  const ccy = a.base_currency ?? "USD";
  return (
    <div className="flex flex-col gap-1.5">
      {a.total_equity != null && (
        <div>
          <div className="font-mono text-[9px] uppercase tracking-wider text-fg-muted/50">
            total equity · {ccy}
          </div>
          <div className="font-mono text-xl tabular-nums leading-none text-fg">
            {fmtNum(a.total_equity)}
          </div>
        </div>
      )}
      <MetricGrid
        items={[
          { label: `cash · ${ccy}`, value: fmtNum(a.cash) },
          ...(a.positions_value != null
            ? [{ label: "positions", value: fmtNum(a.positions_value) }]
            : []),
          ...(a.initial_cash != null && a.total_equity != null
            ? [
                {
                  label: "pnl",
                  value: (
                    <Pnl
                      value={
                        a.total_equity -
                        a.initial_cash -
                        (a.net_external_flows ?? 0)
                      }
                    />
                  ),
                },
              ]
            : []),
        ]}
      />
      {a.perp_margin_locked != null && a.perp_margin_locked > 0 && (
        <div className="rounded-sm border border-gold/40 bg-gold/10 px-1.5 py-1">
          <span className="font-mono text-[10px] text-gold">
            🔒 {fmtNum(a.perp_margin_locked)} {ccy} locked as margin
          </span>
        </div>
      )}
      {a.cash_balances && Object.keys(a.cash_balances).length > 0 && (
        <div className="flex flex-wrap gap-1">
          {Object.entries(a.cash_balances).map(([c, n]) => (
            <span
              key={c}
              className="rounded-sm border border-border-subtle/70 bg-bg/40 px-1 py-px font-mono text-[10px] tabular-nums text-fg-muted"
            >
              {c} {fmtNum(n)}
            </span>
          ))}
        </div>
      )}
      {(a.fx_warnings ?? []).map((w) => (
        <div
          key={w}
          className="rounded-sm border border-gold/40 bg-gold/10 px-1.5 py-1 text-[10px] text-gold"
        >
          {w}
        </div>
      ))}
    </div>
  );
}

export interface PositionShape {
  venue: string;
  symbol: string;
  quantity: number;
  avg_open_price: number;
  realized_pnl?: number;
  currency?: string | null;
}

export function isPositionList(v: unknown): v is PositionShape[] {
  return (
    Array.isArray(v) &&
    v.length > 0 &&
    v.every((o) => {
      const p = o as PositionShape;
      return (
        !!p &&
        typeof p === "object" &&
        typeof p.symbol === "string" &&
        typeof p.quantity === "number" &&
        typeof p.avg_open_price === "number"
      );
    })
  );
}

/** 持仓表:标的 / 数量 / 开仓均价 / 已实现盈亏。 */
export function PositionsView({ list }: { list: PositionShape[] }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full border-collapse font-mono text-[10px] tabular-nums">
        <thead>
          <tr className="text-fg-muted/50">
            {["symbol", "qty", "avg price", "realized pnl"].map((h, i) => (
              <th
                key={h}
                className={cn(
                  "whitespace-nowrap border-b border-border-subtle px-1 py-0.5 font-normal uppercase tracking-wider",
                  i === 0 ? "text-left" : "text-right",
                )}
              >
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {list.map((p, i) => (
            <tr key={`${p.symbol}@${p.venue}-${i}`} className="border-b border-border-subtle/40 last:border-b-0">
              <td className="whitespace-nowrap px-1 py-0.5 text-left text-fg">
                {p.symbol}
                <span className="ml-1 text-fg-muted/50">@{p.venue}</span>
              </td>
              <td className="whitespace-nowrap px-1 py-0.5 text-right text-fg-muted">
                {fmtNum(p.quantity)}
              </td>
              <td className="whitespace-nowrap px-1 py-0.5 text-right text-fg-muted">
                {fmtNum(p.avg_open_price)}
                {p.currency ? ` ${p.currency}` : ""}
              </td>
              <td className="whitespace-nowrap px-1 py-0.5 text-right">
                {p.realized_pnl != null ? <Pnl value={p.realized_pnl} /> : "—"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
