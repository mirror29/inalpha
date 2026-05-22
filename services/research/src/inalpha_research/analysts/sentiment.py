"""Sentiment analyst —— Fear & Greed Index + 反向推理。

设计：

- 数据源：alternative.me 公开 Fear & Greed API（``https://api.alternative.me/fng/``）
  - 0-25 = Extreme Fear（反向 = 利多）
  - 75-100 = Extreme Greed（反向 = 利空）
- analyst 不做硬规则判定，而是把"最新值 + 30 天序列 + 反向 hint"喂给 LLM，
  让 LLM 在 prompt 框定的范围内自己得出 stance + confidence
- 外部网络调用：自起 httpx.AsyncClient（tests 用 respx 拦截 ``api.alternative.me``）
- 失败处理：让异常抛出，由 runner._failed_brief 兜底（标 confidence=0）
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

import httpx

from .base import Analyst

_FNG_URL = "https://api.alternative.me/fng/"
_FETCH_TIMEOUT_S = 10.0
_DEFAULT_LIMIT = 30

_SYSTEM = """
You are a sentiment analyst for crypto markets, specialized in **contrarian reading**
of the Fear & Greed Index.

You receive:
- The most recent Fear & Greed value (0=Extreme Fear, 100=Extreme Greed)
- The last 30 daily values for trend context

Read it **contrarian**:
- < 25 (Extreme Fear)    → moderate bullish bias (capitulation often near bottoms)
- 25-45 (Fear)           → mild bullish bias
- 45-55 (Neutral)        → neutral
- 55-75 (Greed)          → mild bearish bias
- > 75 (Extreme Greed)   → moderate bearish bias (euphoria often near tops)

But adjust by trend:
- Rapidly rising fear → momentum may continue down before reversal
- Sticky greed for many days → already late, lean more bearish

Return ONLY a JSON object with this exact shape:

{
  "stance": "bullish" | "bearish" | "neutral",
  "confidence": float in [0, 1],
  "summary": "1-2 sentence reading",
  "key_points": ["bullet 1", "bullet 2", ...],   // up to 5 items
  "factors": [                                    // 1-2 sentiment factors
    {
      "name": "fng_extreme_fear",                 // snake_case identifier
      "kind": "sentiment",
      "value": 18,                                // the FNG value or trend score
      "strength": 0.7,                            // 0-1 ; higher when extreme + sustained
      "horizon": "swing",
      "explanation": "FNG at 18 (Extreme Fear), 30d avg 35 — contrarian bullish"
    }
  ]
}

Never claim values you weren't given. Confidence and factor.strength should
reflect how extreme + how sustained the reading is.
""".strip()


class SentimentAnalyst(Analyst):
    """Fear & Greed 反向 analyst。"""

    type_id = "sentiment"

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
        entries = await _fetch_fng(limit=_DEFAULT_LIMIT)
        if not entries:
            raise RuntimeError("Fear & Greed API returned no entries")

        latest = entries[0]
        recent_values = [int(e["value"]) for e in entries]
        trend = _summarize_trend(recent_values)

        return _format_user_prompt(
            symbol=symbol,
            as_of=as_of,
            latest=latest,
            recent_values=recent_values,
            trend=trend,
        )


async def _fetch_fng(*, limit: int) -> list[dict[str, Any]]:
    """拉 Fear & Greed 序列，最新在 index 0。

    response 形如 ``{"data": [{"value":"42","value_classification":"Fear","timestamp":"..."}, ...]}``
    """
    async with httpx.AsyncClient(timeout=_FETCH_TIMEOUT_S, trust_env=False) as client:
        r = await client.get(_FNG_URL, params={"limit": limit})
        r.raise_for_status()
        payload = r.json()
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, list):
        raise RuntimeError(f"unexpected Fear & Greed payload shape: {type(payload).__name__}")
    return data


def _summarize_trend(values: list[int]) -> dict[str, Any]:
    """给 LLM 看的小指标：当前值 / 30 日均值 / 最大最小 / 7 日变化。"""
    if not values:
        return {"available": False}
    last = values[0]
    window = values[: min(len(values), 30)]
    avg30 = sum(window) / len(window)
    delta7 = (last - values[7]) if len(values) > 7 else None
    return {
        "available": True,
        "last": last,
        "avg_30d": round(avg30, 1),
        "min_30d": min(window),
        "max_30d": max(window),
        "delta_7d": delta7,
    }


def _format_user_prompt(
    *,
    symbol: str,
    as_of: datetime,
    latest: dict[str, Any],
    recent_values: list[int],
    trend: dict[str, Any],
) -> str:
    return (
        f"asset: {symbol}\n"
        f"as_of: {as_of.isoformat()}\n\n"
        f"latest_fng:\n"
        f"  value: {latest.get('value')}\n"
        f"  classification: {latest.get('value_classification')}\n"
        f"  timestamp: {latest.get('timestamp')}\n\n"
        f"trend_snapshot:\n  {trend}\n\n"
        f"recent_30d_values (newest first):\n  {recent_values}\n\n"
        f"Output the required JSON only."
    )
