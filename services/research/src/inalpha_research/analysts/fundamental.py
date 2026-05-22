"""Fundamental analyst —— 宏观叙事 / 周期判断（D-8b: LLM-only，无外部数据）。

D-8b 范围：只问 LLM "对 X 资产现在的基本面 / 宏观环境怎么看"。
后续 D-9+ 接 sentiment / news 数据源时，再加 ``self._data.get_*`` 调用。
"""
from __future__ import annotations

from datetime import datetime

from .base import Analyst

_SYSTEM = """
You are a fundamental / macro analyst for crypto markets.

You evaluate the medium- to long-term thesis for the given asset based on:
- macro environment (rates, liquidity, regulation)
- on-chain / adoption narrative (briefly)
- supply / demand structure
- known event risks (forks, halvings, ETF flows)

You do NOT use price chart analysis (the technical analyst handles that).

Return ONLY a JSON object with this exact shape:

{
  "stance": "bullish" | "bearish" | "neutral",
  "confidence": float in [0, 1],
  "summary": "1-2 sentence core thesis",
  "key_points": ["bullet 1", "bullet 2", ...],   // up to 5 items
  "factors": [                                    // 1-3 macro / sentiment factors
    {
      "name": "halving_cycle_phase",              // snake_case identifier
      "kind": "macro" | "sentiment",
      "value": "post_halving" | 0.62,             // string label or number
      "strength": 0.5,                            // 0-1
      "horizon": "swing" | "position",
      "explanation": "Within 12 months of last halving, historically bullish"
    }
  ]
}

Rules for factors:
- Output 1-3 factors. Each must be a real macro / on-chain / regulatory driver — not invented prices or events.
- "kind" should be "macro" for monetary / regulation / structural; "sentiment" for adoption / narrative.
- If you lack any specific recent data, lower the strength, do not invent.
- Confidence and factor.strength should reflect data freshness.
""".strip()


class FundamentalAnalyst(Analyst):
    """基本面 analyst。"""

    type_id = "fundamental"

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
        # D-8b 不拉数据；后续 D-9+ 这里会加 sentiment / news 抓取
        return (
            f"asset: {symbol} @ {venue}\n"
            f"as_of: {as_of.isoformat()}\n"
            f"window_days: {lookback_days}\n\n"
            "Output the required JSON only. "
            "Be cautious about claims beyond your training cutoff."
        )
