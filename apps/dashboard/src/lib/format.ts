/**
 * 展示层格式化 —— 客户端安全(无 server-only 依赖)。
 * 金融数字一律等宽对齐(配合 .tnum),正负号显式,locale 走 Intl。
 */

/** 货币金额。大额用紧凑记号(1.2M),小额给 2 位小数。 */
export function fmtMoney(
  value: number,
  ccy = "USD",
  locale = "en",
): string {
  const abs = Math.abs(value);
  const opts: Intl.NumberFormatOptions =
    abs >= 1_000_000
      ? { notation: "compact", maximumFractionDigits: 2 }
      : { minimumFractionDigits: 2, maximumFractionDigits: 2 };
  try {
    return new Intl.NumberFormat(locale, {
      style: "currency",
      currency: ccy,
      currencyDisplay: "narrowSymbol",
      ...opts,
    }).format(value);
  } catch {
    // 非 ISO 货币(如某些 venue 的自定义计价)退回带 code 前缀。
    return `${ccy} ${fmtNum(value, locale)}`;
  }
}

/** 普通数字,千分位 + 最多 N 位小数,trailing zero 去掉。 */
export function fmtNum(value: number, locale = "en", maxFrac = 4): string {
  return new Intl.NumberFormat(locale, {
    maximumFractionDigits: maxFrac,
  }).format(value);
}

/** 带正负号的 PnL —— 用于盈亏显示(+/− 前缀)。 */
export function fmtSigned(
  value: number,
  ccy: string | null,
  locale = "en",
): string {
  const sign = value > 0 ? "+" : value < 0 ? "−" : "";
  const body = ccy
    ? fmtMoney(Math.abs(value), ccy, locale)
    : fmtNum(Math.abs(value), locale, 2);
  return `${sign}${body}`;
}

/** 数量 —— 小数位按量级自适应(0.0012 vs 1500)。 */
export function fmtQty(value: number, locale = "en"): string {
  const abs = Math.abs(value);
  const frac = abs >= 1000 ? 2 : abs >= 1 ? 4 : 8;
  return new Intl.NumberFormat(locale, {
    maximumFractionDigits: frac,
  }).format(value);
}

/** PnL 方向 → tailwind 颜色 class(国际惯例:正绿负红)。 */
export function pnlColor(value: number): string {
  if (value > 0) return "text-bull";
  if (value < 0) return "text-fox-red";
  return "text-fg-muted";
}

/** "BTC/USDT @ binance" 形式的标的标签。 */
export function instrumentLabel(
  symbol: string | null,
  venue: string | null,
): string {
  if (!symbol) return "—";
  return venue ? `${symbol} · ${venue}` : symbol;
}

/** 时间 → 本地时分秒(列表里用)。 */
export function fmtTime(iso: string, locale = "en"): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return new Intl.DateTimeFormat(locale, {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(d);
}

/** 时间 → 年月日时分(详情页用)。 */
export function fmtDateTime(iso: string, locale = "en"): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return new Intl.DateTimeFormat(locale, {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(d);
}

/**
 * 相对时间 "3m ago" / "刚刚"。
 * @param nowMs 传入"现在"的毫秒(由调用方给,便于一致 & 可测)。
 */
export function fmtRelative(
  iso: string | null,
  nowMs: number,
  locale = "en",
): string {
  if (!iso) return "—";
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return "—";
  const diffSec = Math.round((t - nowMs) / 1000);
  const abs = Math.abs(diffSec);
  const rtf = new Intl.RelativeTimeFormat(locale, { numeric: "auto" });
  if (abs < 60) return rtf.format(Math.trunc(diffSec), "second");
  if (abs < 3600) return rtf.format(Math.trunc(diffSec / 60), "minute");
  if (abs < 86_400) return rtf.format(Math.trunc(diffSec / 3600), "hour");
  return rtf.format(Math.trunc(diffSec / 86_400), "day");
}
