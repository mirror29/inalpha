"use client";

import { useState } from "react";
import { useLocale, useTranslations } from "next-intl";
import { ArrowDown, ArrowUp, Minus, TriangleAlert } from "lucide-react";
import useSWR from "swr";

import type { FactorEffectiveness, FactorsPayload } from "@/lib/types";
import { cn } from "@/lib/cn";
import { fmtNum } from "@/lib/format";
import { jsonFetcher } from "@/lib/fetcher";
import { SkeletonBlock } from "@/components/ui/Feedback";
import { Panel } from "@/components/ui/Panel";
import {
  DecayBadge,
  decayState,
  FactorDetailOverlay,
} from "@/components/factors/FactorDetail";

/** 有效性是重计算(拉 720 bar + 算 Rank IC,可达 30s),详情页 2 分钟一刷足够。 */
const REFRESH_MS = 120_000;

/**
 * 模拟盘标的的有效因子面板 —— 按 run 的 venue/symbol/timeframe 实时算 top 有效因子,
 * 并给出衰减状态(近期 IC vs 全样本 IC)。注意这是**标的当前的因子环境**,不是
 * "策略源码内部用了哪些因子"(候选是 LLM 自由代码,因子引用不落库,无从反查)。
 * 点行打开因子详情(说明 + 本标的实测指标)。
 */
export function RunnerFactors({
  venue,
  symbol,
  timeframe,
}: {
  venue: string;
  symbol: string;
  timeframe: string;
}) {
  const t = useTranslations("runners.factors");
  const locale = useLocale();
  const [detail, setDetail] = useState<FactorEffectiveness | null>(null);

  const key = `/api/factors?venue=${encodeURIComponent(venue)}&symbol=${encodeURIComponent(symbol)}&timeframe=${encodeURIComponent(timeframe)}`;
  const { data, error, isLoading } = useSWR<FactorsPayload>(key, jsonFetcher, {
    refreshInterval: REFRESH_MS,
    keepPreviousData: true,
    revalidateOnFocus: false,
  });

  if (isLoading && !data) {
    return <SkeletonBlock className="h-48" />;
  }

  const eff = data?.effectiveness ?? null;
  const unavailable = Boolean(error) || !eff || !eff.available;

  return (
    <Panel
      title={t("title")}
      aside={
        <span className="tnum font-mono text-xs text-fg-muted">
          {symbol} · {timeframe}
          {eff?.available ? ` · ${t("barsUsed", { n: eff.bars_used })}` : ""}
        </span>
      }
    >
      <p className="border-b border-border-subtle/60 px-4 py-2 text-[11px] text-fg-muted/70">
        {t("hint")}
      </p>
      {unavailable ? (
        <div className="flex flex-col items-center gap-2 px-4 py-8 text-center">
          <TriangleAlert className="size-5 text-gold/70" strokeWidth={1.5} />
          <p className="text-sm text-fg-muted">
            {eff?.reason ?? t("unavailable")}
          </p>
        </div>
      ) : eff.top_factors.length === 0 ? (
        <p className="px-4 py-8 text-center text-sm text-fg-muted/70">
          {t("empty")}
        </p>
      ) : (
        <div className="overflow-x-auto">
          <div className="min-w-[560px]">
            <FactorHeader />
            <ul className="divide-y divide-border-subtle/40">
              {eff.top_factors.map((f) => (
                <li key={f.factor_id}>
                  <button
                    type="button"
                    onClick={() => setDetail(f)}
                    title={t("viewDetail")}
                    className="flex w-full items-center gap-3 px-4 py-2 text-left transition-colors hover:bg-bg-elev/40"
                  >
                    {/* 名称 + kind + 样本不足标记 */}
                    <div className="min-w-36 flex-1">
                      <div className="truncate text-sm text-fg" title={f.factor_id}>
                        {f.name}
                      </div>
                      <div className="font-mono text-[10px] uppercase tracking-wider text-fg-muted/60">
                        {f.kind}
                        {f.low_confidence && <span className="ml-1 text-gold">· low-n</span>}
                      </div>
                    </div>
                    <DirectionMark dir={f.direction} />
                    <Num value={f.rank_ic} digits={3} locale={locale} signed colored />
                    <Num
                      value={f.rank_ic_recent ?? 0}
                      digits={3}
                      locale={locale}
                      signed
                      colored
                    />
                    <Num value={f.icir} digits={2} locale={locale} />
                    <Num value={f.turnover ?? 0} digits={2} locale={locale} />
                    <DecayBadge state={decayState(f)} />
                  </button>
                </li>
              ))}
            </ul>
          </div>
        </div>
      )}

      <FactorDetailOverlay
        target={
          detail
            ? {
                factor_id: detail.factor_id,
                name: detail.name,
                kind: detail.kind,
                source: detail.source,
                direction: detail.direction,
                eff: detail,
              }
            : null
        }
        onClose={() => setDetail(null)}
      />
    </Panel>
  );
}

/** 列头 —— 与数据行同布局(flex + 固定列宽)对齐。 */
function FactorHeader() {
  const t = useTranslations("runners.factors");
  return (
    <div className="flex items-center gap-3 border-b border-border-subtle/60 px-4 py-1.5 font-mono text-[10px] uppercase tracking-[0.14em] text-fg-muted/60">
      <span className="min-w-36 flex-1">{t("colFactor")}</span>
      <span className="w-3.5" />
      <span className="w-16 text-right">{t("colIc")}</span>
      <span className="w-16 text-right">{t("colRecent")}</span>
      <span className="w-12 text-right">ICIR</span>
      <span className="w-12 text-right">{t("colTurnover")}</span>
      <span className="w-14 text-center">{t("colDecay")}</span>
    </div>
  );
}

function Num({
  value,
  digits,
  locale,
  signed,
  colored,
}: {
  value: number;
  digits: number;
  locale: string;
  signed?: boolean;
  colored?: boolean;
}) {
  return (
    <span
      className={cn(
        "tnum shrink-0 text-right font-mono text-xs",
        signed ? "w-16" : "w-12",
        colored ? (value >= 0 ? "text-bull" : "text-fox-red") : "text-fg-muted",
      )}
    >
      {signed ? (value >= 0 ? "+" : "−") : ""}
      {fmtNum(Math.abs(value), locale, digits)}
    </span>
  );
}

function DirectionMark({ dir }: { dir: number }) {
  if (dir > 0) return <ArrowUp className="size-3.5 shrink-0 text-bull" strokeWidth={2.5} />;
  if (dir < 0) return <ArrowDown className="size-3.5 shrink-0 text-fox-red" strokeWidth={2.5} />;
  return <Minus className="size-3.5 shrink-0 text-fg-muted/50" strokeWidth={2.5} />;
}
