"use client";

import { cn } from "@/lib/cn";

import { shortDate } from "./format";
import { SymbolHeader } from "./primitives";

/**
 * research.deep_dive 视图:评级 + 置信 + 论点 + 风险 + 操作建议。
 * 取代过去 JSON 转义后糊成一坨的长文。形态不符回 null 由通用 ToolOutput 兜底。
 */

interface ResearchShape {
  research_id: string;
  symbol: string;
  timeframe?: string;
  as_of?: string;
  rating: "overweight" | "neutral" | "underweight" | string;
  confidence?: number;
  thesis: string;
  risks?: string[];
  suggested_action?: string;
  factors?: unknown[];
}

export function isResearch(v: unknown): v is ResearchShape {
  const o = v as ResearchShape;
  return (
    !!o &&
    typeof o === "object" &&
    typeof o.research_id === "string" &&
    typeof o.thesis === "string" &&
    typeof o.rating === "string"
  );
}

/** 评级 → 颜色档(overweight 绿 / underweight 红 / neutral 中性)。 */
function RatingBadge({ rating }: { rating: string }) {
  const tone =
    rating === "overweight"
      ? "text-bull border-bull/40 bg-bull/10"
      : rating === "underweight"
        ? "text-fox-red border-fox-red/40 bg-fox-red/10"
        : "text-fg-muted border-border-subtle bg-bg/40";
  return (
    <span
      className={cn(
        "rounded-sm border px-1.5 py-px font-mono text-[10px] uppercase tracking-wider",
        tone,
      )}
    >
      {rating}
    </span>
  );
}

const MAX_RISKS = 4;

export function ResearchView({ r }: { r: ResearchShape }) {
  const risks = (r.risks ?? []).filter((x): x is string => typeof x === "string");
  const shown = risks.slice(0, MAX_RISKS);
  return (
    <div className="flex flex-col gap-1.5">
      <SymbolHeader
        symbol={r.symbol}
        tags={[r.timeframe, r.as_of ? `as_of ${shortDate(r.as_of)}` : null]}
        right={
          typeof r.confidence === "number" ? (
            <span className="font-mono text-[10px] text-fg-muted/60">
              conf {(r.confidence * 100).toFixed(0)}%
            </span>
          ) : undefined
        }
      />
      <div>
        <RatingBadge rating={r.rating} />
      </div>
      <p className="text-[11px] leading-relaxed text-fg">{r.thesis}</p>

      {shown.length > 0 && (
        <div>
          <div className="font-mono text-[9px] uppercase tracking-wider text-fg-muted/50">
            risks
          </div>
          <ul className="mt-0.5 space-y-0.5">
            {shown.map((x, i) => (
              <li key={i} className="flex gap-1.5 text-[11px] text-fg-muted">
                <span className="text-fox-red">·</span>
                <span className="min-w-0 flex-1">{x}</span>
              </li>
            ))}
            {risks.length > shown.length && (
              <li className="font-mono text-[10px] text-fg-muted/40">
                +{risks.length - shown.length} 更多
              </li>
            )}
          </ul>
        </div>
      )}

      {r.suggested_action && (
        <div className="rounded-sm border border-border-subtle/60 bg-bg/40 px-2 py-1">
          <div className="font-mono text-[9px] uppercase tracking-wider text-fg-muted/50">
            suggested action
          </div>
          <p className="mt-0.5 font-mono text-[11px] text-cyan">{r.suggested_action}</p>
        </div>
      )}
    </div>
  );
}
