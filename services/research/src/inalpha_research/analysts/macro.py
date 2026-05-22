"""Macro analyst —— 宏观环境 + 近期日程。

设计：

- **跟 fundamental 的区分**：
  - ``fundamental`` 关注**该标的**自身（halving / ETF flows / on-chain）
  - ``macro``      关注**宏观环境**（Fed rate / DXY / 地缘 / 当周日程）
- 数据：硬编码 `_MACRO_CALENDAR`（近期高影响事件列表）
  - 维护方式：作者手动追加 / 移除条目；过期条目可以保留作为历史 context
  - 列入：FOMC / CPI / NFP / 大型加密事件（半减 / ETF 决议）
- LLM 拿 ``as_of ± 14 天`` 范围内的事件 + 自身宏观训练知识，输出 stance
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from .base import Analyst

#: 高影响宏观事件硬编码列表。
#:
#: 维护原则：
#:
#: - 列入：FOMC（议息）/ CPI / NFP / Fed Chair speech / G7 / G20 / 重大加密法案
#: - 不列入：常规财报 / 单一国家小事件 / 日内数据点
#:
#: 字段：``date`` ISO 日期、``name`` 简称、``impact`` ``"high"`` / ``"med"``、
#: ``note`` 1 句话背景。
_MACRO_CALENDAR: list[dict[str, str]] = [
    {"date": "2026-05-07", "name": "FOMC rate decision",        "impact": "high", "note": "May FOMC; market priced in hold + dovish dots"},
    {"date": "2026-05-13", "name": "US April CPI",              "impact": "high", "note": "Headline CPI for April release"},
    {"date": "2026-06-06", "name": "US May NFP",                "impact": "high", "note": "Non-farm payrolls"},
    {"date": "2026-06-18", "name": "FOMC rate decision",        "impact": "high", "note": "June FOMC; first cut window per Fed funds futures"},
    {"date": "2026-07-29", "name": "FOMC rate decision",        "impact": "high", "note": "July FOMC"},
    {"date": "2026-09-17", "name": "FOMC rate decision",        "impact": "high", "note": "September FOMC"},
    {"date": "2026-11-03", "name": "US presidential election",  "impact": "high", "note": "Crypto policy direction depends on result"},
]

_SYSTEM = """
You are a macro analyst for crypto markets.

You evaluate **the macro environment** (Fed policy, USD strength, liquidity,
geopolitics, election cycles) and how it shapes the next 1-12 weeks of crypto
risk appetite. You do NOT analyze price charts (technical) or asset-specific
narrative (fundamental).

You receive a near-term macro calendar plus the asset + as_of context. Combine
that with your own macro training knowledge to output a stance.

Reference reading:
- Imminent FOMC + hawkish surprise risk      → bearish bias on crypto risk
- FOMC dovish / first-cut announced          → bullish bias
- US CPI surprise upside                     → bearish (delays cuts)
- Strong DXY rally context                   → bearish for crypto
- High geopolitical / election uncertainty   → neutral with elevated risk

Return ONLY a JSON object with this exact shape:

{
  "stance": "bullish" | "bearish" | "neutral",
  "confidence": float in [0, 1],
  "summary": "1-2 sentence macro read",
  "key_points": ["bullet 1", "bullet 2", ...],   // up to 5 items
  "factors": [                                    // 1-3 macro factors
    {
      "name": "fomc_imminent",                    // snake_case identifier
      "kind": "macro",
      "value": "high",                            // numeric or label
      "strength": 0.7,                            // 0-1
      "horizon": "swing",
      "explanation": "FOMC in 4 days; market pricing dovish pivot"
    }
  ]
}

If your training cutoff is older than as_of, say so in summary; confidence
should reflect that uncertainty. All macro factor.kind should be "macro".
""".strip()


class MacroAnalyst(Analyst):
    """宏观环境 analyst（不打 K 线，只看日程 + 训练知识）。"""

    type_id = "macro"

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
        events = _events_in_window(as_of=as_of, before_days=14, after_days=14)
        return _format_user_prompt(
            venue=venue,
            symbol=symbol,
            as_of=as_of,
            events=events,
        )


def _events_in_window(
    *,
    as_of: datetime,
    before_days: int,
    after_days: int,
) -> list[dict[str, str]]:
    """筛 ``_MACRO_CALENDAR`` 里 ``[as_of - before_days, as_of + after_days]`` 范围内的事件。"""
    lo = (as_of - timedelta(days=before_days)).date()
    hi = (as_of + timedelta(days=after_days)).date()
    out: list[dict[str, str]] = []
    for ev in _MACRO_CALENDAR:
        try:
            d = datetime.fromisoformat(ev["date"]).replace(tzinfo=UTC).date()
        except (ValueError, KeyError):
            continue
        if lo <= d <= hi:
            out.append(ev)
    return out


def _format_user_prompt(
    *,
    venue: str,
    symbol: str,
    as_of: datetime,
    events: list[dict[str, Any]],
) -> str:
    if events:
        ev_lines = "\n".join(
            f"  - {e.get('date')} | {e.get('name')} | impact={e.get('impact')} | {e.get('note')}"
            for e in events
        )
        ev_block = f"upcoming_macro_events (±14 days):\n{ev_lines}"
    else:
        ev_block = "upcoming_macro_events (±14 days):\n  (none in window)"

    return (
        f"asset: {symbol} @ {venue}\n"
        f"as_of: {as_of.isoformat()}\n\n"
        f"{ev_block}\n\n"
        f"Output the required JSON only."
    )
