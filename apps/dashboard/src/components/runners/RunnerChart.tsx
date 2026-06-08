"use client";

import { useTranslations } from "next-intl";
import useSWR from "swr";

import type { BarsPayload, StrategyRunDecisionRecord } from "@/lib/types";
import { jsonFetcher } from "@/lib/fetcher";
import { Panel } from "@/components/ui/Panel";
import { CandlestickChart } from "./CandlestickChart";

/** K 线随 bar 推进,20s 一刷。 */
const REFRESH_MS = 20_000;

/**
 * Live Runner 详情的 K 线面板 —— 取该 run 标的的最近 K 线,把决策点叠在蜡烛上。
 * 图是辅助信息:取不到 / 为空只显示占位,不影响详情页其余部分。
 */
export function RunnerChart({
  venue,
  symbol,
  timeframe,
  decisions,
}: {
  venue: string;
  symbol: string;
  timeframe: string;
  decisions: StrategyRunDecisionRecord[];
}) {
  const t = useTranslations("runners.detail");
  const key = `/api/bars?venue=${encodeURIComponent(venue)}&symbol=${encodeURIComponent(symbol)}&timeframe=${encodeURIComponent(timeframe)}&limit=300`;
  const { data, error } = useSWR<BarsPayload>(key, jsonFetcher, {
    refreshInterval: REFRESH_MS,
    keepPreviousData: true,
  });

  const bars = data?.bars ?? [];

  return (
    <Panel
      title={t("chart")}
      aside={
        <span className="font-mono text-[11px] text-fg-muted">
          {symbol} · {timeframe}
        </span>
      }
    >
      {bars.length === 0 ? (
        <div className="px-4 py-12 text-center text-sm text-fg-muted/70">
          {error ? t("chartError") : t("chartEmpty")}
        </div>
      ) : (
        <div className="px-2 py-2">
          <CandlestickChart bars={bars} decisions={decisions} />
        </div>
      )}
    </Panel>
  );
}
