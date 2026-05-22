"""Technical analyst —— K 线 + 简单指标（SMA / RSI）支撑下的短期立场。"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from .base import Analyst

_SYSTEM = """
You are a technical analyst for crypto markets.

You receive recent OHLCV bars and basic indicator hints. Your job is to output a
short-term (intraday to swing) stance based **only on price action and technicals**.
You must not invoke fundamentals or news.

Return ONLY a JSON object with this exact shape:

{
  "stance": "bullish" | "bearish" | "neutral",
  "confidence": float in [0, 1],
  "summary": "1-2 sentence core conclusion",
  "key_points": ["bullet 1", "bullet 2", ...]  // up to 5 items
}

Be terse. Refuse to fabricate indicators that weren't given.
""".strip()


class TechnicalAnalyst(Analyst):
    """技术分析 analyst。"""

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

        # 提炼最近 N 根 + 算几个粗指标喂给 LLM（避免直接喂全量 K 线，太长）
        recent = bars[-60:]  # 最多最近 60 根
        closes = [float(b["close"]) for b in recent]
        snapshot = _build_indicator_snapshot(closes)

        return _format_user_prompt(
            venue=venue,
            symbol=symbol,
            timeframe=timeframe,
            as_of=as_of,
            num_bars=len(bars),
            recent=recent,
            snapshot=snapshot,
        )


def _build_indicator_snapshot(closes: list[float]) -> dict[str, Any]:
    """计算简单指标快照（无 numpy 依赖）。"""
    n = len(closes)
    if n == 0:
        return {"available": False}

    last = closes[-1]
    sma20 = sum(closes[-20:]) / min(n, 20)
    sma50 = sum(closes[-50:]) / min(n, 50) if n >= 20 else None

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
) -> str:
    """简洁、tokens 友好的格式。"""
    last_lines = "\n".join(
        f"  - {b.get('ts')} O={b.get('open')} H={b.get('high')} L={b.get('low')} "
        f"C={b.get('close')} V={b.get('volume')}"
        for b in recent[-10:]
    )
    return (
        f"asset: {symbol} @ {venue}\n"
        f"timeframe: {timeframe}\n"
        f"as_of: {as_of.isoformat()}\n"
        f"bars_total: {num_bars}\n\n"
        f"indicator_snapshot:\n  {snapshot}\n\n"
        f"last_10_bars:\n{last_lines}\n\n"
        f"Output the required JSON only."
    )
