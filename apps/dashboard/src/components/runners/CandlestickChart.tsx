"use client";

import { useEffect, useRef, useState } from "react";
import {
  ColorType,
  createChart,
  type CandlestickData,
  type IChartApi,
  type ISeriesApi,
  type SeriesMarker,
  type UTCTimestamp,
} from "lightweight-charts";

import type { BarPoint, StrategyRunDecisionRecord } from "@/lib/types";

/**
 * lightweight-charts 用 canvas 渲染,只吃字面色值(不认 color-mix),所以这里
 * 从 globals.css 的原始主题变量(均为纯 hex)实时读取 —— 切主题即随之换色。
 */
type ChartColors = {
  bull: string;
  fox: string;
  gold: string;
  grid: string;
  text: string;
  border: string;
};

function readColors(): ChartColors {
  const s = getComputedStyle(document.documentElement);
  const v = (name: string, fallback: string) =>
    s.getPropertyValue(name).trim() || fallback;
  const border = v("--hairline", "#1f2740");
  return {
    bull: v("--up", "#2fcf8e"),
    fox: v("--down", "#f0584b"),
    gold: v("--gold", "#e0b03f"),
    text: v("--ink-muted", "#8089a0"),
    border,
    grid: border, // 网格线复用 hairline(canvas 安全的纯 hex)
  };
}

const toSec = (iso: string) => Math.floor(new Date(iso).getTime() / 1000) as UTCTimestamp;

/**
 * 蜡烛图 + 决策打点。决策按 side 定上下、按 outcome 定颜色;marker 时间吸附到
 * ≤ 该时刻的最近一根 K 线,避免与蜡烛错位。
 */
export function CandlestickChart({
  bars,
  decisions = [],
  height = 320,
}: {
  bars: BarPoint[];
  decisions?: StrategyRunDecisionRecord[];
  height?: number;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  // 主题色(惰性读取,SSR 不跑;切 data-theme 时由 MutationObserver 更新)。
  const [C, setColors] = useState<ChartColors | null>(null);

  // 首挂读色 + 监听主题切换。
  useEffect(() => {
    setColors(readColors());
    const obs = new MutationObserver(() => setColors(readColors()));
    obs.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ["data-theme"],
    });
    return () => obs.disconnect();
  }, []);

  // 建图(高度或主题变化时重建)。
  useEffect(() => {
    const el = containerRef.current;
    if (!el || !C) return;

    const chart = createChart(el, {
      width: el.clientWidth,
      height,
      layout: {
        background: { type: ColorType.Solid, color: "transparent" },
        textColor: C.text,
        fontFamily: "var(--font-mono)",
      },
      grid: {
        vertLines: { color: C.grid },
        horzLines: { color: C.grid },
      },
      rightPriceScale: { borderColor: C.border },
      timeScale: {
        borderColor: C.border,
        timeVisible: true,
        secondsVisible: false,
      },
      crosshair: { mode: 0 },
    });
    const series = chart.addCandlestickSeries({
      upColor: C.bull,
      downColor: C.fox,
      borderVisible: false,
      wickUpColor: C.bull,
      wickDownColor: C.fox,
    });
    chartRef.current = chart;
    seriesRef.current = series;

    const ro = new ResizeObserver((entries) => {
      const w = entries[0]?.contentRect.width;
      if (w) chart.applyOptions({ width: Math.floor(w) });
    });
    ro.observe(el);

    return () => {
      ro.disconnect();
      chart.remove();
      chartRef.current = null;
      seriesRef.current = null;
    };
  }, [height, C]);

  // 灌数据 + 决策打点。
  useEffect(() => {
    const series = seriesRef.current;
    const chart = chartRef.current;
    if (!series || !chart || !C) return;

    // 去重 + 升序(图表要求时间严格递增)。
    const seen = new Set<number>();
    const candles: CandlestickData[] = [];
    for (const b of bars) {
      const time = toSec(b.ts);
      if (seen.has(time)) continue;
      seen.add(time);
      candles.push({ time, open: b.open, high: b.high, low: b.low, close: b.close });
    }
    series.setData(candles);

    // 决策 → marker,时间吸附到 ≤ 该时刻的最近一根 K 线。
    const times = candles.map((c) => c.time as number).sort((a, b) => a - b);
    const snap = (sec: number): number | null => {
      let lo = 0,
        hi = times.length - 1,
        ans: number | null = null;
      while (lo <= hi) {
        const mid = (lo + hi) >> 1;
        if (times[mid] <= sec) {
          ans = times[mid];
          lo = mid + 1;
        } else hi = mid - 1;
      }
      return ans;
    };

    const markers: SeriesMarker<UTCTimestamp>[] = [];
    for (const d of decisions) {
      const t = snap(toSec(d.bar_ts) as number);
      if (t === null) continue;
      const buy = d.side === "BUY";
      const color =
        d.outcome === "filled" ? (buy ? C.bull : C.fox) : C.gold;
      markers.push({
        time: t as UTCTimestamp,
        position: buy ? "belowBar" : "aboveBar",
        shape: buy ? "arrowUp" : "arrowDown",
        color,
        text: d.intent ?? d.side,
      });
    }
    markers.sort((a, b) => (a.time as number) - (b.time as number));
    series.setMarkers(markers);

    chart.timeScale().fitContent();
  }, [bars, decisions, C]);

  return <div ref={containerRef} className="w-full" style={{ height }} />;
}
