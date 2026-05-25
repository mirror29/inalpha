"""Technical analyst —— K 线 + 简单指标（SMA / RSI）支撑下的短期立场。

D-9 起：multi-market 感知——同一个 analyst 在 crypto / 美股 / A 股 / 港股 / 全球 5 类
资产上自动调整指标解读（market_type 由 ``researchers.base.infer_asset_type`` 推断后
塞进 user prompt）。计算指标的 Python 代码完全通用（OHLCV 都一样），只在 prompt 上
做差别提示。
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from ..researchers.base import infer_asset_type
from .base import Analyst

_SYSTEM = """
You are a technical analyst covering any asset class.

You receive recent OHLCV bars + indicator snapshot, plus the ``market_type``
(crypto / us_stock / cn_stock / hk_stock / global_stock). Adjust your reading
to the market's micro-structure conventions:

| market_type    | Notes                                                                 |
|----------------|-----------------------------------------------------------------------|
| crypto         | 24/7 markets, RSI 70/30 hard; gap-less; vol regimes shift fast        |
| us_stock       | Cash hours; RSI 65/35 useful; pre/post-market gap risk; SPY/VIX peers |
| cn_stock       | T+1, 涨跌停 ±10% (科创板 ±20%), open-call auction; 成交量 quality      |
| hk_stock       | T+0 cash; ADR/H-share arbitrage; HK rate sensitivity                  |
| global_stock   | Local cash hours; FX-translation distort; thinner liquidity tail      |

Use **only price action and technicals** —— do not invoke fundamentals or news.

Return ONLY a JSON object with this exact shape:

{
  "stance": "bullish" | "bearish" | "neutral",
  "confidence": float in [0, 1],
  "summary": "1-2 sentence core conclusion",
  "key_points": ["bullet 1", "bullet 2", ...],   // up to 5 items
  "factors": [                                    // 2-4 structured factors
    {
      "name": "sma20_above_sma50",                // snake_case identifier
      "kind": "momentum" | "mean_reversion" | "volatility" | "macro" | "sentiment",
      "value": 1.02,                              // numeric, OR "high"/"medium"/"low"
      "strength": 0.7,                            // 0-1, how strong this factor is
      "horizon": "intraday" | "swing" | "position",
      "explanation": "20-bar SMA crossed above 50-bar by 2%"
    }
  ]
}

Rules for factors:
- Output 2-4 factors. Each must be derivable from the indicator snapshot or bars given.
- "kind" classifies the factor mechanism:
    momentum         = trend / breakout / cross  (e.g. SMA cross, MACD up)
    mean_reversion   = RSI extreme, distance from moving average, BB squeeze
    volatility       = ATR spike, range expansion / contraction
- Numeric "value" is preferred; only use the string buckets when no number applies.
- Be terse. Refuse to fabricate indicators that weren't given.
""".strip()


class TechnicalAnalyst(Analyst):
    """技术分析 analyst（multi-market 感知）。"""

    type_id = "technical"

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

        # 提炼最近 N 根 + 算几个粗指标喂给 LLM
        recent = bars[-60:]
        closes = [float(b["close"]) for b in recent]
        snapshot = _build_indicator_snapshot(closes)

        market_type = infer_asset_type(venue=venue, symbol=symbol)

        return _format_user_prompt(
            venue=venue,
            symbol=symbol,
            timeframe=timeframe,
            as_of=as_of,
            num_bars=len(bars),
            recent=recent,
            snapshot=snapshot,
            market_type=market_type,
        )


def _build_indicator_snapshot(closes: list[float]) -> dict[str, Any]:
    """计算简单指标快照（无 numpy 依赖）。"""
    n = len(closes)
    if n == 0:
        return {"available": False}

    last = closes[-1]
    # SMA：样本不够时返 None 而不是"半段平均"（D-8b' review B14）
    sma20 = sum(closes[-20:]) / 20 if n >= 20 else None
    sma50 = sum(closes[-50:]) / 50 if n >= 50 else None

    # 简化 RSI(14)：n < 15 直接 None
    rsi14: float | None = None
    if n >= 15:
        gains: list[float] = []
        losses: list[float] = []
        for i in range(1, 15):
            diff = closes[-i] - closes[-i - 1]
            if diff > 0:
                gains.append(diff)
            else:
                losses.append(-diff)
        avg_gain = sum(gains) / 14 if gains else 0.0
        avg_loss = sum(losses) / 14 if losses else 0.0
        if avg_loss == 0:
            rsi14 = 100.0 if avg_gain > 0 else 50.0
        else:
            rs = avg_gain / avg_loss
            rsi14 = 100.0 - 100.0 / (1.0 + rs)

    return {
        "available": True,
        "last_close": last,
        "sma20": sma20,
        "sma50": sma50,
        "rsi14": rsi14,
        "pct_change_5bar": _pct_change(closes, 5),
        "pct_change_20bar": _pct_change(closes, 20),
    }


def _pct_change(closes: list[float], lookback: int) -> float | None:
    if len(closes) < lookback + 1:
        return None
    base = closes[-lookback - 1]
    if base <= 0:
        return None
    return (closes[-1] / base - 1.0) * 100.0


def _format_user_prompt(
    *,
    venue: str,
    symbol: str,
    timeframe: str,
    as_of: datetime,
    num_bars: int,
    recent: list[dict[str, Any]],
    snapshot: dict[str, Any],
    market_type: str,
) -> str:
    """简洁、tokens 友好的格式。"""
    last_lines = "\n".join(
        f"  - {b.get('ts')} O={b.get('open')} H={b.get('high')} L={b.get('low')} "
        f"C={b.get('close')} V={b.get('volume')}"
        for b in recent[-10:]
    )
    return (
        f"asset: {symbol} @ {venue}\n"
        f"market_type: {market_type}\n"
        f"timeframe: {timeframe}\n"
        f"as_of: {as_of.isoformat()}\n"
        f"bars_total: {num_bars}\n\n"
        f"indicator_snapshot:\n  {snapshot}\n\n"
        f"last_10_bars:\n{last_lines}\n\n"
        f"Output the required JSON only."
    )
