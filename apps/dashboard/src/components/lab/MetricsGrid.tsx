"use client";

import { useLocale } from "next-intl";

import { cn } from "@/lib/cn";
import { fmtNum, pnlColor } from "@/lib/format";

/**
 * 候选 / 回测指标格。键随策略路径变化,只渲染存在的已知指标(标准量化术语,
 * 中英通用,不另做 i18n)。fitness 是多目标合成,单列突出;不展示裸 equity 曲线
 * (后端历史无 equity_curve 接口)。
 */

type Fmt = "ratio" | "pct" | "pctSigned" | "int";

const SPEC: Array<{ key: string; label: string; fmt: Fmt }> = [
  { key: "total_return_pct", label: "RETURN", fmt: "pctSigned" },
  { key: "sharpe", label: "SHARPE", fmt: "ratio" },
  { key: "sortino", label: "SORTINO", fmt: "ratio" },
  { key: "calmar", label: "CALMAR", fmt: "ratio" },
  { key: "max_drawdown_pct", label: "MAX DD", fmt: "pct" },
  { key: "win_rate", label: "WIN RATE", fmt: "pct" },
  { key: "num_trades", label: "TRADES", fmt: "int" },
  { key: "num_bars_processed", label: "BARS", fmt: "int" },
];

export function MetricsGrid({
  metrics,
  fitness,
  className,
}: {
  metrics: Record<string, number> | null;
  fitness: number | null;
  className?: string;
}) {
  const locale = useLocale();
  const present = SPEC.filter(
    (s) => metrics && typeof metrics[s.key] === "number",
  );

  const fmt = (v: number, kind: Fmt): { text: string; cls?: string } => {
    switch (kind) {
      case "pctSigned": {
        const sign = v > 0 ? "+" : v < 0 ? "−" : "";
        return { text: `${sign}${fmtNum(Math.abs(v), locale, 2)}%`, cls: pnlColor(v) };
      }
      case "pct":
        return { text: `${fmtNum(v, locale, 2)}%` };
      case "int":
        return { text: fmtNum(v, locale, 0) };
      case "ratio":
      default:
        return { text: fmtNum(v, locale, 2), cls: pnlColor(v) };
    }
  };

  return (
    <div
      className={cn(
        "grid grid-cols-2 gap-x-4 gap-y-3 sm:grid-cols-3 lg:grid-cols-5",
        className,
      )}
    >
      <Cell
        label="FITNESS"
        text={fitness === null ? "—" : fmtNum(fitness, locale, 3)}
        cls={fitness === null ? "text-fg-muted/50" : pnlColor(fitness)}
        accent
      />
      {present.map((s) => {
        const { text, cls } = fmt(metrics![s.key], s.fmt);
        return <Cell key={s.key} label={s.label} text={text} cls={cls} />;
      })}
      {present.length === 0 && fitness === null && (
        <span className="col-span-full font-mono text-xs text-fg-muted/60">
          no backtest metrics yet
        </span>
      )}
    </div>
  );
}

function Cell({
  label,
  text,
  cls,
  accent,
}: {
  label: string;
  text: string;
  cls?: string;
  accent?: boolean;
}) {
  return (
    <div>
      <div
        className={cn(
          "font-mono text-[10px] uppercase tracking-[0.14em]",
          accent ? "text-cyan/80" : "text-fg-muted/60",
        )}
      >
        {label}
      </div>
      <div className={cn("tnum mt-0.5 font-mono text-base", cls ?? "text-fg")}>
        {text}
      </div>
    </div>
  );
}
