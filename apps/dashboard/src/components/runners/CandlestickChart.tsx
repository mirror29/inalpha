"use client";

import { useEffect, useRef, useState } from "react";
import {
  ColorType,
  createChart,
  LineStyle,
  type CandlestickData,
  type HistogramData,
  type IChartApi,
  type ISeriesApi,
  type LineData,
  type SeriesMarker,
  type UTCTimestamp,
} from "lightweight-charts";

import type { BarPoint, StrategyRunDecisionRecord } from "@/lib/types";
import { cn } from "@/lib/cn";

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

// ── 技术指标(纯前端从收盘价算,可开关) ────────────────────────────────
type OverlayKey = "ma5" | "ma10" | "ma20" | "ma60" | "ema" | "boll" | "vol";

const LINE_COLORS: Record<string, string> = {
  ma5: "#f5a623",
  ma10: "#4f9dde",
  ma20: "#9b8cff",
  ma60: "#e056a0",
  boll: "#8089a0",
};
const EMA_PERIOD = 20;
const BOLL_PERIOD = 20;
const BOLL_MULT = 2;

/** 简单移动均线。 */
function sma(closes: number[], times: UTCTimestamp[], period: number): LineData[] {
  const out: LineData[] = [];
  let sum = 0;
  for (let i = 0; i < closes.length; i += 1) {
    sum += closes[i];
    if (i >= period) sum -= closes[i - period];
    if (i >= period - 1) out.push({ time: times[i], value: sum / period });
  }
  return out;
}

/** 指数移动均线(以前 period 根的 SMA 作种子)。 */
function ema(closes: number[], times: UTCTimestamp[], period: number): LineData[] {
  const out: LineData[] = [];
  const k = 2 / (period + 1);
  let prev = 0;
  let seed = 0;
  for (let i = 0; i < closes.length; i += 1) {
    if (i < period) {
      seed += closes[i];
      if (i === period - 1) {
        prev = seed / period;
        out.push({ time: times[i], value: prev });
      }
      continue;
    }
    prev = closes[i] * k + prev * (1 - k);
    out.push({ time: times[i], value: prev });
  }
  return out;
}

/** 布林带上下轨(中轨=SMA20,与 MA20 重复故不画)。 */
function bollinger(
  closes: number[],
  times: UTCTimestamp[],
  period: number,
  mult: number,
): { upper: LineData[]; lower: LineData[] } {
  const upper: LineData[] = [];
  const lower: LineData[] = [];
  for (let i = period - 1; i < closes.length; i += 1) {
    let sum = 0;
    for (let j = i - period + 1; j <= i; j += 1) sum += closes[j];
    const mean = sum / period;
    let varSum = 0;
    for (let j = i - period + 1; j <= i; j += 1) varSum += (closes[j] - mean) ** 2;
    const sd = Math.sqrt(varSum / period);
    upper.push({ time: times[i], value: mean + mult * sd });
    lower.push({ time: times[i], value: mean - mult * sd });
  }
  return { upper, lower };
}

const OVERLAYS: { key: OverlayKey; label: string }[] = [
  { key: "ma5", label: "MA5" },
  { key: "ma10", label: "MA10" },
  { key: "ma20", label: "MA20" },
  { key: "ma60", label: "MA60" },
  { key: "ema", label: `EMA${EMA_PERIOD}` },
  { key: "boll", label: "BOLL" },
  { key: "vol", label: "VOL" },
];

const MA_PERIODS: Record<string, number> = { ma5: 5, ma10: 10, ma20: 20, ma60: 60 };

/** 当前价格摘要(末根收盘 + 较上一根的涨跌幅)。 */
interface PriceInfo {
  last: number;
  changePct: number;
}

/** 价格按量级取小数位,避免大币种一堆 0、小币种丢精度。 */
function fmtPrice(p: number): string {
  const abs = Math.abs(p);
  const digits = abs >= 1000 ? 2 : abs >= 1 ? 2 : abs >= 0.01 ? 4 : 6;
  return p.toLocaleString(undefined, {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

/**
 * 蜡烛图 + 决策打点 + 技术辅助线(MA5/10/20/60 / EMA / 布林带 / 成交量,可开关)+ 当前价。
 * 决策按 side 定上下、按 outcome 定颜色;marker 时间吸附到 ≤ 该时刻的最近一根 K 线。
 * 末根收盘价以虚线 priceLine 标在右轴(当前价),并在工具条左侧大字显示 + 涨跌幅。
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
  // 当前叠加的辅助线 / 成交量 series(开关 / 重建时清掉重画)。
  const overlayRef = useRef<ISeriesApi<"Line" | "Histogram">[]>([]);
  const [C, setColors] = useState<ChartColors | null>(null);
  const [info, setInfo] = useState<PriceInfo | null>(null);
  const [on, setOn] = useState<Record<OverlayKey, boolean>>({
    ma5: false,
    ma10: false,
    ma20: true,
    ma60: false,
    ema: false,
    boll: false,
    vol: true,
  });

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

    // canvas 的 ctx.font 不认 CSS 变量 —— 把 --font-mono 解析成真实字体串再传,
    // 否则 lightweight-charts 拿 "var(--font-mono)" 解析失败、回退到默认字体(渲染发虚 / 串味)。
    const fontFamily =
      getComputedStyle(el).getPropertyValue("--font-mono").trim() ||
      "ui-monospace, SFMono-Regular, Menlo, monospace";

    const chart = createChart(el, {
      width: el.clientWidth,
      height,
      layout: {
        background: { type: ColorType.Solid, color: "transparent" },
        textColor: C.text,
        fontFamily,
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
      priceLineVisible: true, // 末值价格线 = 当前价
      priceLineStyle: LineStyle.Dashed,
      priceLineColor: C.gold,
      priceLineWidth: 1,
    });
    chartRef.current = chart;
    seriesRef.current = series;
    overlayRef.current = []; // 旧 chart 已销毁,辅助线 series 引用作废

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
      overlayRef.current = [];
    };
  }, [height, C]);

  // 灌数据 + 决策打点 + 辅助线 + 当前价。
  useEffect(() => {
    const series = seriesRef.current;
    const chart = chartRef.current;
    if (!series || !chart || !C) return;

    // 去重 + 升序(图表要求时间严格递增)。
    const seen = new Set<number>();
    const candles: CandlestickData[] = [];
    const volumes: HistogramData[] = [];
    for (const b of bars) {
      const time = toSec(b.ts);
      if (seen.has(time)) continue;
      seen.add(time);
      candles.push({ time, open: b.open, high: b.high, low: b.low, close: b.close });
      volumes.push({
        time,
        value: b.volume,
        color: b.close >= b.open ? `${C.bull}66` : `${C.fox}66`,
      });
    }
    series.setData(candles);

    // 当前价 + 涨跌幅(末根 vs 前一根收盘)。
    if (candles.length > 0) {
      const last = candles[candles.length - 1].close;
      const prev = candles.length > 1 ? candles[candles.length - 2].close : last;
      setInfo({ last, changePct: prev ? (last - prev) / prev : 0 });
    } else {
      setInfo(null);
    }

    // 辅助线 / 成交量:先清掉上一轮 series,再按当前开关重画。
    for (const s of overlayRef.current) chart.removeSeries(s);
    overlayRef.current = [];
    const closes = candles.map((c) => c.close);
    const times = candles.map((c) => c.time as UTCTimestamp);
    const addLine = (data: LineData[], color: string) => {
      if (data.length === 0) return;
      const s = chart.addLineSeries({
        color,
        lineWidth: 1,
        priceLineVisible: false,
        lastValueVisible: false,
        crosshairMarkerVisible: false,
      });
      s.setData(data);
      overlayRef.current.push(s);
    };

    for (const key of ["ma5", "ma10", "ma20", "ma60"] as const) {
      if (on[key]) addLine(sma(closes, times, MA_PERIODS[key]), LINE_COLORS[key]);
    }
    if (on.ema) addLine(ema(closes, times, EMA_PERIOD), C.gold);
    if (on.boll) {
      const { upper, lower } = bollinger(closes, times, BOLL_PERIOD, BOLL_MULT);
      addLine(upper, LINE_COLORS.boll);
      addLine(lower, LINE_COLORS.boll);
    }

    // 成交量 —— 独立价格刻度,贴底部约 1/5 高度;主价刻度据此留底边。
    if (on.vol) {
      const vol = chart.addHistogramSeries({
        priceScaleId: "vol",
        priceFormat: { type: "volume" },
        priceLineVisible: false,
        lastValueVisible: false,
      });
      vol.setData(volumes);
      chart.priceScale("vol").applyOptions({
        scaleMargins: { top: 0.82, bottom: 0 },
      });
      overlayRef.current.push(vol);
    }
    series.priceScale().applyOptions({
      scaleMargins: on.vol ? { top: 0.06, bottom: 0.24 } : { top: 0.1, bottom: 0.1 },
    });

    // 决策 → marker,时间吸附到 ≤ 该时刻的最近一根 K 线。
    const sortedTimes = times.slice().sort((a, b) => a - b);
    const snap = (sec: number): number | null => {
      let lo = 0,
        hi = sortedTimes.length - 1,
        ans: number | null = null;
      while (lo <= hi) {
        const mid = (lo + hi) >> 1;
        if (sortedTimes[mid] <= sec) {
          ans = sortedTimes[mid];
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
      const color = d.outcome === "filled" ? (buy ? C.bull : C.fox) : C.gold;
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
  }, [bars, decisions, C, on]);

  const up = (info?.changePct ?? 0) >= 0;

  return (
    // dir=ltr 钉死整块为从左到右 —— canvas 文字方向继承 DOM direction，上游若为 rtl
    // 会让十字线/轴上的价格、时间气泡从右往左排。这里强制 LTR。
    <div dir="ltr" className="flex flex-col gap-2">
      {/* 工具条:当前价 + 辅助线开关 */}
      <div className="flex flex-wrap items-center justify-between gap-2 px-1">
        {info ? (
          <div className="flex items-baseline gap-2">
            <span className="tnum font-mono text-lg font-medium tracking-tight text-fg">
              {fmtPrice(info.last)}
            </span>
            <span
              className={cn(
                "tnum font-mono text-xs",
                up ? "text-bull" : "text-fox-red",
              )}
            >
              {up ? "+" : ""}
              {(info.changePct * 100).toFixed(2)}%
            </span>
          </div>
        ) : (
          <span />
        )}
        <div className="flex flex-wrap items-center gap-1">
          {OVERLAYS.map((o) => (
            <button
              key={o.key}
              type="button"
              onClick={() => setOn((s) => ({ ...s, [o.key]: !s[o.key] }))}
              aria-pressed={on[o.key]}
              className={cn(
                "rounded border px-1.5 py-0.5 font-mono text-[10px] uppercase tracking-wider transition-colors",
                on[o.key]
                  ? "border-cyan/40 bg-cyan/10 text-cyan"
                  : "border-border-subtle text-fg-muted/70 hover:text-fg",
              )}
            >
              {o.label}
            </button>
          ))}
        </div>
      </div>
      {/* dir=ltr：canvas 文字方向默认继承元素 direction，强制 LTR，避免轴/十字线
          气泡里的价格、时间从右往左排（继承到 rtl 时会这样）。 */}
      <div ref={containerRef} dir="ltr" className="w-full" style={{ height }} />
    </div>
  );
}
