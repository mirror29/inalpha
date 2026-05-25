"""Risk analyst —— 波动率 / 回撤 / 极端价差，给 manager 一票否决信号。

D-9 起：multi-market 感知——同一个 analyst 在 crypto / 美股 / A 股 / 港股 / 全球 5 类
资产上自动调整波动率阈值（market_type 由 ``researchers.base.infer_asset_type`` 推断）。

设计：

- 指标计算（ATR / max DD / vol z-score）完全通用，对任何 OHLCV 都一致
- 阈值不一样：crypto 4% ATR 算正常，美股 4% 就是 vol spike → prompt 里给 5 类表，
  LLM 按 market_type 选区间
- analyst 的 stance 语义跟其他人不同：
  - ``bullish`` = 风险可控（vol 低 / DD 小），管理层可以放心加仓
  - ``bearish`` = 风险偏高（vol 飙 / 深 DD / 接近历史峰值），manager 应减仓
  - ``neutral`` = 中等
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta
from typing import Any

from ..researchers.base import infer_asset_type
from .base import Analyst

_SYSTEM = """
You are a risk analyst covering any asset class.

You receive recent OHLCV bars + computed risk metrics + ``market_type``
(crypto / us_stock / cn_stock / hk_stock / global_stock). Use the per-market
volatility band table to decide whether risk is contained or elevated.

Risk-aware stance semantics (different from technical / sentiment analysts!):
- "bullish" = **risk is contained** — moderate vol, no fresh deep drawdown
- "bearish" = **risk is elevated** — vol spike, deep recent drawdown, or fragile structure
- "neutral" = mixed signals

Per-market ATR/close bands (hourly bars; scale for daily by ~sqrt(24x)):

| market_type    | low vol   | normal       | high vol (lean bearish) | fragility DD |
|----------------|-----------|--------------|-------------------------|---------------|
| crypto         | < 1.5%    | 1.5 – 3.5%   | > 3.5%                  | > 15%         |
| us_stock       | < 0.8%    | 0.8 – 2.0%   | > 2.0%                  | > 10%         |
| cn_stock       | < 1.0%    | 1.0 – 2.5%   | > 2.5%                  | > 12%         |
| hk_stock       | < 1.0%    | 1.0 – 2.5%   | > 2.5%                  | > 12%         |
| global_stock   | < 1.0%    | 1.0 – 2.5%   | > 2.5%                  | > 12%         |

Universal: vol z-score (short vs long) > 2 → outlier, recent vol spike (lean bearish).

Return ONLY a JSON object with this exact shape:

{
  "stance": "bullish" | "bearish" | "neutral",
  "confidence": float in [0, 1],
  "summary": "1-2 sentence risk read",
  "key_points": ["bullet 1", "bullet 2", ...],   // up to 5 items
  "factors": [                                    // 1-3 risk factors
    {
      "name": "atr_pct_14",                       // snake_case identifier
      "kind": "volatility",
      "value": 2.3,                               // numeric or "high"/"med"/"low"
      "strength": 0.5,                            // 0-1 ; higher = signal is stronger
      "horizon": "swing",
      "explanation": "ATR/close 2.3% — normal vol band for us_stock"
    }
  ]
}

Never invent numbers not in the snapshot. Confidence and factor.strength should
be lower when the window is short or signals conflict. All risk factor.kind
should be "volatility".
""".strip()


class RiskAnalyst(Analyst):
    """波动率 / 回撤 风险 analyst（multi-market 感知）。"""

    type_id = "risk"

    def system_prompt(self) -> str:
        return _SYSTEM

    async def build_user_prompt(
        self,
        *,
        venue: str,
        symbol: str,
        timeframe: str,
        as_of: datetime,
        lookback_days: int,
    ) -> str:
        from_ts = as_of - timedelta(days=lookback_days)
        bars = await self._data.get_bars(
            venue=venue,
            symbol=symbol,
            timeframe=timeframe,
            from_ts=from_ts,
            to_ts=as_of,
            limit=2_000,
        )
        snapshot = _build_risk_snapshot(bars)
        market_type = infer_asset_type(venue=venue, symbol=symbol)
        return _format_user_prompt(
            venue=venue,
            symbol=symbol,
            timeframe=timeframe,
            as_of=as_of,
            num_bars=len(bars),
            snapshot=snapshot,
            market_type=market_type,
        )


def _build_risk_snapshot(bars: list[dict[str, Any]]) -> dict[str, Any]:
    """计算风险指标快照（无 numpy 依赖）。"""
    n = len(bars)
    if n == 0:
        return {"available": False, "reason": "no bars"}

    highs = [float(b["high"]) for b in bars]
    lows = [float(b["low"]) for b in bars]
    closes = [float(b["close"]) for b in bars]

    last_close = closes[-1]
    atr14 = _atr(highs, lows, closes, period=14)
    atr_pct = (atr14 / last_close * 100.0) if (atr14 is not None and last_close > 0) else None

    max_dd_pct = _max_drawdown_pct(closes)

    vol_z = _vol_z_score(closes, short=14, long_=min(100, n))

    return {
        "available": True,
        "last_close": last_close,
        "atr14": atr14,
        "atr_pct_of_close": round(atr_pct, 3) if atr_pct is not None else None,
        "max_drawdown_pct": round(max_dd_pct, 3) if max_dd_pct is not None else None,
        "vol_zscore_14_vs_long": round(vol_z, 3) if vol_z is not None else None,
        "bars_used": n,
    }


def _atr(highs: list[float], lows: list[float], closes: list[float], *, period: int) -> float | None:
    """简化 ATR：``mean(true_range)`` over last ``period`` 根。"""
    n = len(closes)
    if n < period + 1:
        return None
    trs: list[float] = []
    for i in range(n - period, n):
        h, lo = highs[i], lows[i]
        prev_close = closes[i - 1]
        tr = max(h - lo, abs(h - prev_close), abs(lo - prev_close))
        trs.append(tr)
    return sum(trs) / len(trs)


def _max_drawdown_pct(closes: list[float]) -> float | None:
    """最大回撤百分比（正数表示跌幅）。"""
    if len(closes) < 2:
        return None
    peak = closes[0]
    max_dd = 0.0
    for c in closes:
        if c > peak:
            peak = c
        if peak > 0:
            dd = (peak - c) / peak * 100.0
            if dd > max_dd:
                max_dd = dd
    return max_dd


def _vol_z_score(closes: list[float], *, short: int, long_: int) -> float | None:
    """短期波动率 vs 长期波动率的 z-score。"""
    if len(closes) < long_ + 1 or short >= long_:
        return None
    rets = [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes)) if closes[i - 1] > 0]
    if len(rets) < long_:
        return None
    long_rets = rets[-long_:]
    short_rets = rets[-short:]

    long_std = _stdev(long_rets)
    short_std = _stdev(short_rets)
    if long_std == 0:
        return None

    return (short_std - long_std) / long_std


def _stdev(xs: list[float]) -> float:
    n = len(xs)
    if n < 2:
        return 0.0
    mean = sum(xs) / n
    var = sum((x - mean) ** 2 for x in xs) / (n - 1)
    return math.sqrt(var)


def _format_user_prompt(
    *,
    venue: str,
    symbol: str,
    timeframe: str,
    as_of: datetime,
    num_bars: int,
    snapshot: dict[str, Any],
    market_type: str,
) -> str:
    return (
        f"asset: {symbol} @ {venue}\n"
        f"market_type: {market_type}\n"
        f"timeframe: {timeframe}\n"
        f"as_of: {as_of.isoformat()}\n"
        f"bars_total: {num_bars}\n\n"
        f"risk_snapshot:\n  {snapshot}\n\n"
        f"Output the required JSON only."
    )
