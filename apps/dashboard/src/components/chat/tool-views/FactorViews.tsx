"use client";

import { cn } from "@/lib/cn";

import { fmtNum, fmtSigned, shortDate } from "./format";
import { CollapseSection, MetricGrid, SymbolHeader } from "./primitives";

/**
 * factor 域工具视图(D-12 因子库):因子有效性打分 / 自定义表达式因子评估。
 * 每个视图带 shape guard,形态不符回 null 由通用 ToolOutput 兜底。
 */

// ── 共享:单因子有效性 ─────────────────────────────────────────────

interface FactorEffectiveness {
  factor_id: string;
  name: string;
  kind?: string;
  rank_ic: number;
  rank_ic_recent?: number;
  icir?: number;
  turnover?: number;
  direction?: number;
  strength?: number;
  low_confidence?: boolean;
  decay_state?: "stable" | "fading" | "decaying";
}

function isEffectiveness(v: unknown): v is FactorEffectiveness {
  const o = v as FactorEffectiveness;
  return (
    !!o &&
    typeof o === "object" &&
    typeof o.factor_id === "string" &&
    typeof o.rank_ic === "number"
  );
}

/** 衰减三态 → 颜色(stable 绿 / fading 金 / decaying 红);无则不渲染。 */
function DecayBadge({ state }: { state?: string }) {
  if (!state) return null;
  const tone =
    state === "stable"
      ? "text-bull border-bull/40 bg-bull/10"
      : state === "decaying"
        ? "text-fox-red border-fox-red/40 bg-fox-red/10"
        : "text-gold border-gold/40 bg-gold/10";
  return (
    <span
      className={cn(
        "shrink-0 rounded-sm border px-1 py-px font-mono text-[9px] uppercase tracking-wider",
        tone,
      )}
    >
      {state}
    </span>
  );
}

/** 方向箭头:+1 看多(绿▲)/ -1 看空(红▼)/ 0 中性(—)。 */
function DirArrow({ direction }: { direction?: number }) {
  const d = direction ?? 0;
  return (
    <span
      className={cn(
        "shrink-0 font-mono text-[11px]",
        d > 0 ? "text-bull" : d < 0 ? "text-fox-red" : "text-fg-muted/50",
      )}
      aria-hidden
    >
      {d > 0 ? "▲" : d < 0 ? "▼" : "—"}
    </span>
  );
}

/** 单因子行:方向 + 名称 + Rank IC + 衰减态。低置信度弱化。 */
function FactorRow({ f }: { f: FactorEffectiveness }) {
  return (
    <div
      className={cn(
        "flex items-center gap-2 rounded-sm border border-border-subtle/60 bg-bg/40 px-1.5 py-1",
        f.low_confidence && "opacity-60",
      )}
    >
      <DirArrow direction={f.direction} />
      <span className="min-w-0 flex-1 truncate text-[11px] text-fg" title={f.factor_id}>
        {f.name}
      </span>
      <span className="shrink-0 font-mono text-[10px] tabular-nums text-fg-muted">
        IC {fmtSigned(f.rank_ic)}
      </span>
      <DecayBadge state={f.decay_state} />
    </div>
  );
}

// ── factor.timing / factor.score:因子有效性榜 ────────────────────

interface FactorScoreShape {
  symbol: string;
  timeframe: string;
  as_of?: string;
  horizon_bars?: number;
  bars_used?: number;
  factors?: FactorEffectiveness[];
  top_factors?: FactorEffectiveness[];
  ic_null_benchmark?: number;
  candidates_evaluated?: number;
  low_confidence_count?: number;
  reason?: string | null;
}

/** factor.score 用 factors、factor.timing(snapshot)用 top_factors,二者择一。 */
function factorList(v: FactorScoreShape): FactorEffectiveness[] {
  const arr = v.factors ?? v.top_factors;
  return Array.isArray(arr) ? arr.filter(isEffectiveness) : [];
}

export function isFactorScore(v: unknown): v is FactorScoreShape {
  const o = v as FactorScoreShape;
  if (!o || typeof o !== "object" || typeof o.symbol !== "string") return false;
  const arr = o.factors ?? o.top_factors;
  return Array.isArray(arr) && arr.length > 0 && arr.every(isEffectiveness);
}

const MAX_FACTORS = 12;

export function FactorScoreView({ s }: { s: FactorScoreShape }) {
  const factors = factorList(s);
  const shown = factors.slice(0, MAX_FACTORS);
  return (
    <div className="flex flex-col gap-1.5">
      <SymbolHeader
        symbol={s.symbol}
        tags={[s.timeframe, s.as_of ? `as_of ${shortDate(s.as_of)}` : null]}
        right={
          typeof s.ic_null_benchmark === "number" ? (
            <span className="font-mono text-[10px] text-fg-muted/60">
              noise |IC| {fmtNum(s.ic_null_benchmark)}
            </span>
          ) : undefined
        }
      />
      <div className="flex flex-col gap-1">
        {shown.map((f) => (
          <FactorRow key={f.factor_id} f={f} />
        ))}
      </div>
      {(factors.length > shown.length ||
        typeof s.candidates_evaluated === "number" ||
        typeof s.low_confidence_count === "number") && (
        <div className="flex flex-wrap gap-x-3 font-mono text-[10px] text-fg-muted/50">
          {factors.length > shown.length && <span>+{factors.length - shown.length} 更多</span>}
          {typeof s.candidates_evaluated === "number" && (
            <span>{s.candidates_evaluated} 候选评估</span>
          )}
          {typeof s.low_confidence_count === "number" && s.low_confidence_count > 0 && (
            <span>{s.low_confidence_count} 低置信</span>
          )}
        </div>
      )}
    </div>
  );
}

// ── factor.evaluate_candidate:自定义表达式因子 ───────────────────

interface CustomFactorShape {
  symbol: string;
  timeframe: string;
  expression: string;
  available?: boolean;
  reason?: string | null;
  factor?: FactorEffectiveness | null;
  ic_pvalue?: number | null;
  max_corr?: number | null;
  is_likely_redundant?: boolean;
  top_correlated?: { factor_id: string; corr: number }[];
}

export function isCustomFactor(v: unknown): v is CustomFactorShape {
  const o = v as CustomFactorShape;
  return (
    !!o &&
    typeof o === "object" &&
    typeof o.expression === "string" &&
    typeof o.is_likely_redundant === "boolean"
  );
}

export function CustomFactorView({ c }: { c: CustomFactorShape }) {
  const f = c.factor && isEffectiveness(c.factor) ? c.factor : null;
  const metrics: { label: string; value: React.ReactNode }[] = [];
  if (f) {
    metrics.push({ label: "rank ic", value: fmtSigned(f.rank_ic) });
    if (typeof f.icir === "number") metrics.push({ label: "icir", value: fmtNum(f.icir) });
    if (typeof c.ic_pvalue === "number")
      metrics.push({ label: "p-value", value: fmtNum(c.ic_pvalue) });
    if (typeof f.turnover === "number")
      metrics.push({ label: "turnover", value: fmtNum(f.turnover) });
  }

  return (
    <div className="flex flex-col gap-1.5">
      <SymbolHeader
        symbol={c.symbol}
        tags={[c.timeframe]}
        right={f ? <DecayBadge state={f.decay_state} /> : undefined}
      />
      <pre className="overflow-x-auto rounded-sm border border-border-subtle/60 bg-bg-deep/40 px-2 py-1 font-mono text-[11px] text-cyan">
        {c.expression}
      </pre>

      {c.available === false ? (
        <p className="font-mono text-[11px] text-fox-red">
          {c.reason ?? "因子不可用"}
        </p>
      ) : (
        <>
          <MetricGrid items={metrics} />
          {c.is_likely_redundant && (
            <p className="rounded-sm border border-gold/40 bg-gold/10 px-2 py-1 text-[11px] text-gold">
              疑似已有因子换皮
              {typeof c.max_corr === "number" ? `（|corr| ${fmtNum(c.max_corr)}）` : ""}
            </p>
          )}
          {c.top_correlated && c.top_correlated.length > 0 && (
            <CollapseSection label="top correlated" hint={`${c.top_correlated.length}`}>
              <div className="flex flex-col gap-0.5">
                {c.top_correlated.map((t) => (
                  <div
                    key={t.factor_id}
                    className="flex items-center gap-2 font-mono text-[10px] text-fg-muted"
                  >
                    <span className="min-w-0 flex-1 truncate">{t.factor_id}</span>
                    <span className="tabular-nums">{fmtNum(t.corr)}</span>
                  </div>
                ))}
              </div>
            </CollapseSection>
          )}
        </>
      )}
    </div>
  );
}
