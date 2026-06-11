"use client";

import { useTranslations } from "next-intl";
import { useState } from "react";
import useSWR from "swr";

import type { BarsPayload, StrategyRunDecisionRecord } from "@/lib/types";
import { cn } from "@/lib/cn";
import { jsonFetcher } from "@/lib/fetcher";
import { Panel } from "@/components/ui/Panel";
import { CandlestickChart } from "./CandlestickChart";

/** K 线随 bar 推进,20s 一刷。 */
const REFRESH_MS = 20_000;

/** 可选周期 —— 覆盖常见档；run 自身周期若不在内会自动并入(始终可切回)。 */
const TF_CHOICES = ["1m", "5m", "15m", "30m", "1h", "4h", "1d", "1wk"];

/**
 * Live Runner 详情的 K 线面板 —— 取该 run 标的的最近 K 线,把决策点叠在蜡烛上。
 *
 * 周期可切:默认按 run 自身 timeframe,用户可临时切到其他周期看不同尺度的走势
 * (决策 marker 仍按时间吸附到最近 bar)。图是辅助信息,取不到 / 为空只显示占位。
 *
 * @param title 面板标题覆盖 —— lab 候选详情页同屏还有「回测 K 线」,要传
 *   「K 线(最近 run)」区分语义;不传用 runners 详情页的通用「K 线」。
 */
export function RunnerChart({
  venue,
  symbol,
  timeframe,
  decisions,
  title,
}: {
  venue: string;
  symbol: string;
  timeframe: string;
  decisions: StrategyRunDecisionRecord[];
  title?: string;
}) {
  const t = useTranslations("runners.detail");
  const [tf, setTf] = useState(timeframe);
  const choices = TF_CHOICES.includes(timeframe)
    ? TF_CHOICES
    : [timeframe, ...TF_CHOICES];

  const key = `/api/bars?venue=${encodeURIComponent(venue)}&symbol=${encodeURIComponent(symbol)}&timeframe=${encodeURIComponent(tf)}&limit=300`;
  const { data, error, isValidating } = useSWR<BarsPayload>(key, jsonFetcher, {
    refreshInterval: REFRESH_MS,
    keepPreviousData: true,
  });

  const bars = data?.bars ?? [];
  // 切周期后 data 仍是旧周期的(keepPreviousData)→ 显示的与选中的不一致 = 正在拉新周期。
  // 首次切到没回填过的周期要 backfill,可能十几~几十秒,这期间给明确「加载中」反馈,
  // 否则看着像「切了没反应」(其实在拉数据)。
  const switching = data?.timeframe !== tf && isValidating && !error;
  // 切周期/刷新失败但 keepPreviousData 仍留着旧 bars：图照常画但内容与选中周期不符,
  // 给个淡提示避免「按钮高亮 1d、图却是 1h」的静默误导(CR)。
  const staleError = !!error && !switching && bars.length > 0;
  // 进一步区分:周期切换失败时图上是**别的周期**(data.timeframe ≠ 选中 tf),提示要点名
  // 实际在显示哪个周期;同周期刷新失败则只是旧快照。两者文案不同,避免语义误导(CR)。
  const staleTf = data?.timeframe;
  const staleSwitch = staleError && staleTf !== tf;

  return (
    <Panel
      title={title ?? t("chart")}
      aside={
        <div className="flex items-center gap-2">
          {switching && (
            <span className="flex items-center gap-1 font-mono text-[10px] text-cyan">
              <span className="size-1.5 rounded-full bg-cyan caret-blink" />
              {t("chartLoading")}
            </span>
          )}
          <div className="flex items-center gap-0.5 rounded-md border border-border-subtle bg-bg/40 p-0.5">
            {choices.map((c) => (
              <button
                key={c}
                type="button"
                onClick={() => setTf(c)}
                aria-pressed={tf === c}
                className={cn(
                  "rounded px-1.5 py-0.5 font-mono text-[10px] uppercase tracking-wider transition-colors",
                  tf === c
                    ? "bg-cyan/15 text-cyan"
                    : "text-fg-muted/70 hover:text-fg",
                )}
              >
                {c}
              </button>
            ))}
          </div>
        </div>
      }
    >
      {bars.length === 0 ? (
        <div className="px-4 py-12 text-center text-sm text-fg-muted/70">
          {switching ? t("chartLoading") : error ? t("chartError") : t("chartEmpty")}
        </div>
      ) : (
        <div className="relative px-2 py-2">
          <CandlestickChart bars={bars} decisions={decisions} />
          {/* 拉新周期时旧图压暗 + 角标,明确「在加载,不是没反应」。 */}
          {switching && (
            <div className="pointer-events-none absolute inset-0 flex items-start justify-center bg-bg-deep/30 pt-6">
              <span className="flex items-center gap-1.5 rounded-md border border-cyan/30 bg-bg-elev/90 px-2.5 py-1 font-mono text-[11px] text-cyan">
                <span className="size-1.5 rounded-full bg-cyan caret-blink" />
                {t("chartLoading")}
              </span>
            </div>
          )}
          {/* 刷新/切周期失败、图上是旧数据：角标提示,不静默误导。切周期失败时点名实际周期。 */}
          {staleError && (
            <div className="pointer-events-none absolute inset-0 flex items-start justify-center pt-6">
              <span className="flex items-center gap-1.5 rounded-md border border-gold/30 bg-bg-elev/90 px-2.5 py-1 font-mono text-[11px] text-gold">
                <span className="size-1.5 rounded-full bg-gold" />
                {staleSwitch
                  ? t("chartStaleSwitch", { tf: staleTf ?? "" })
                  : t("chartStale")}
              </span>
            </div>
          )}
        </div>
      )}
    </Panel>
  );
}
