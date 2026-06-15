/**
 * 工具输出可视化的共享格式化工具 —— ToolOutput(通用)与 tool-views/*(分工具)共用。
 */

/** ISO 时间戳 → "YYYY-MM-DD HH:mm[:ss]"(去 T / 毫秒 / 时区);不是时间戳返回 null。 */
export function shortTimestamp(s: string): string | null {
  const m = s.match(
    /^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2}(?::\d{2})?)(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?$/,
  );
  return m ? `${m[1]} ${m[2]}` : null;
}

/** 日期部分("YYYY-MM-DD");解析不了原样返回。 */
export function shortDate(s: string): string {
  return shortTimestamp(s)?.slice(0, 10) ?? s;
}

/**
 * 金融数字显示:千分位 + 按量级控制小数位(≥1 两位、<1 最多六位、极小走科学计数)。
 * 不做四舍五入语义承诺 —— 仅展示层,精确值悬浮 title / raw 里看。
 */
export function fmtNum(n: number): string {
  if (!Number.isFinite(n)) return String(n);
  if (Number.isInteger(n))
    return Math.abs(n) >= 10000 ? n.toLocaleString("en-US") : String(n);
  const abs = Math.abs(n);
  if (abs > 0 && abs < 0.0001) return n.toExponential(2);
  return n.toLocaleString("en-US", {
    maximumFractionDigits: abs >= 1 ? 2 : 6,
  });
}

/** 带正负号的数字(盈亏 / alpha 类),0 不带号。 */
export function fmtSigned(n: number): string {
  const s = fmtNum(Math.abs(n));
  return n > 0 ? `+${s}` : n < 0 ? `-${s}` : s;
}

/** URL → 裸 host(去 www.);解析失败原样返回截断。 */
export function hostOf(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return url.slice(0, 40);
  }
}

/** 紧凑单行 JSON,截断到 n 字符。 */
export function compact(v: unknown, n = 80): string {
  let s: string;
  try {
    s = JSON.stringify(v) ?? String(v);
  } catch {
    s = String(v);
  }
  return s.length > n ? `${s.slice(0, n)}…` : s;
}

/** UUID → 短显示(前 8 位)。 */
export function shortId(s: string): string {
  return /^[0-9a-f]{8}-[0-9a-f]{4}/.test(s) ? s.slice(0, 8) : s;
}
