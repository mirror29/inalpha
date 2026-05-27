"""Macro analyst —— 宏观环境 + 近期日程（multi-market 感知）。

D-9 起：

- **跟 fundamental 的区分**：
  - ``fundamental`` 关注**该标的**自身（halving / ETF flows / 财报 / 政策）
  - ``macro``      关注**宏观环境**（Fed rate / DXY / 地缘 / 当周日程）+ 不同市场的传导
- 数据：硬编码 `_MACRO_CALENDAR`（近期高影响事件列表，以美国为主）
- LLM 拿 ``as_of ± 14 天`` 范围内的事件 + market_type → 输出按市场传导调整的 stance

不同市场对同一 macro 事件的反应不同（FOMC 鹰派对美股直接打、对 crypto 高敏感、
对 A 股次级影响、港股因 USD-peg 直传）—— 由 LLM 在 system prompt 的传导表内自适应。
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from ..researchers.base import infer_asset_type
from .base import Analyst

#: 高影响宏观事件硬编码列表。
#:
#: 维护原则：
#: - 列入：FOMC（议息）/ CPI / NFP / Fed Chair speech / G7 / G20 / 重大加密法案
#: - 不列入：常规财报 / 单一国家小事件 / 日内数据点
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
You are a macro analyst covering any asset class.

You evaluate the macro environment (Fed policy, USD strength, liquidity,
geopolitics, election cycles) and how it shapes the next 1-12 weeks of
risk appetite **in the given market_type**. You do NOT analyze price charts
(technical) or asset-specific narrative (fundamental).

**HARD CONSTRAINT — DATA TRUTHFULNESS (D-9)**:

You only receive **event NAMES + DATES** in the calendar (no actual outcomes,
no realized prints, no current price levels). The system has **NO live feed**
for macro indicators (DXY, CPI prints, rate decisions, NFP numbers).

Therefore you MUST NOT:
- Claim specific directional outcomes you weren't told ("CPI surprised upside",
  "DXY rallying", "Fed cut by 25bps", "EUR/USD at 1.08") — there is **no data
  for these claims**; you'd be hallucinating from training-time knowledge.
- Quote specific numbers / percentages / pip moves for any indicator unless
  they appear verbatim in the user prompt's calendar/news section.
- Treat past dates in the calendar as "already-released with known results" —
  the calendar entries (both past_macro_events_last_14d and
  upcoming_macro_events_next_14d) are NAMES + DATES only; outcomes are NOT
  included for either group. For past events you know they HAVE happened, but
  you DO NOT know the surprise direction.
- Refer to past_macro_events_last_14d events with PAST tense ("CPI was
  released on ..."), NEVER with "即将" / "upcoming" / "this week" phrasing.
  Refer to upcoming_macro_events_next_14d with FUTURE / conditional phrasing.

You MAY:
- Describe **regime / risk framing** based on the event schedule ("CPI release
  this week — outcome unknown, hence elevated uncertainty").
- Use **conditional / hypothetical** phrasing ("**IF** hawkish surprise → ...").
- Reference your training-time knowledge **with explicit "as of training" caveat**
  + lower confidence.

You receive a near-term macro calendar (US-centric — FOMC / CPI / NFP / election)
plus the asset + as_of + ``market_type`` (crypto / us_stock / cn_stock /
hk_stock / global_stock). Apply the cross-market transmission table:

| market_type    | Transmission of hawkish FOMC / strong USD                                |
|----------------|-------------------------------------------------------------------------|
| crypto         | **high sensitivity** — risk-off + DXY rally compress crypto             |
| us_stock       | **direct hit** — discount rate up / multiple compress, financials nuance |
| cn_stock       | **secondary** — RMB pressure + PBOC reaction; sector rotation matters    |
| hk_stock       | **direct (USD-peg)** — HKD-rates track Fed verbatim; HK property hits    |
| global_stock   | **mixed** — depends on local rates / FX-hedged demand                    |

Reference reading (universal):
- Imminent FOMC + hawkish surprise risk      → bearish bias
- FOMC dovish / first-cut announced          → bullish bias
- US CPI surprise upside                     → bearish (delays cuts globally)
- Strong DXY rally context                   → bearish (esp. crypto + HKD-linked)
- High geopolitical / election uncertainty   → neutral with elevated risk

Return ONLY a JSON object with this exact shape:

{
  "stance": "bullish" | "bearish" | "neutral",
  "confidence": float in [0, 1],
  "summary": "1-2 sentence macro read for THIS market_type",
  "key_points": ["bullet 1", "bullet 2", ...],   // up to 5 items
  "factors": [                                    // 1-3 macro factors
    {
      "name": "fomc_imminent",                    // snake_case identifier
      "kind": "macro",
      "value": "high",                            // numeric or label
      "strength": 0.7,                            // 0-1
      "horizon": "swing",
      "explanation": "FOMC in 4 days; dovish pivot priced — bullish for crypto, mixed US"
    }
  ]
}

If your training cutoff is older than as_of, say so in summary; confidence
should reflect that uncertainty. All macro factor.kind should be "macro".
Be explicit about how each event translates to the current market_type.

**Confidence ceiling without live data**: when **no live macro feed** is in the
user prompt (only event dates), cap your ``confidence`` at **0.5**. Higher
confidence requires actual data points cited verbatim.
""".strip()


class MacroAnalyst(Analyst):
    """宏观环境 analyst（multi-market 传导感知；不打 K 线，只看日程 + 训练知识）。"""

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
        market_type = infer_asset_type(venue=venue, symbol=symbol)
        # D-9 L3：拉 SPY 当宏观 proxy（美国宏观环境主导全球风险偏好）。
        # 拉不到时返空 list，prompt 里清晰标注，LLM 走纯 calendar + 训练知识。
        macro_news = await self._data.get_news(symbol="SPY", limit=8)
        return _format_user_prompt(
            venue=venue,
            symbol=symbol,
            as_of=as_of,
            events=events,
            market_type=market_type,
            macro_news=macro_news,
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
    market_type: str,
    macro_news: list[dict[str, Any]],
) -> str:
    # 按 as_of 把事件拆成 past / upcoming —— 避免 LLM 把 14 天前已发生的 CPI 说成"即将"
    as_of_date = as_of.date()
    past_events: list[dict[str, Any]] = []
    upcoming_events: list[dict[str, Any]] = []
    for ev in events:
        try:
            ev_date = datetime.fromisoformat(str(ev.get("date"))).date()
        except (ValueError, TypeError):
            continue
        (past_events if ev_date < as_of_date else upcoming_events).append(ev)

    def _fmt(evs: list[dict[str, Any]]) -> str:
        return "\n".join(
            f"  - {e.get('date')} | {e.get('name')} | impact={e.get('impact')} | {e.get('note')}"
            for e in evs
        )

    past_block = (
        f"past_macro_events_last_14d (calendar, NAMES ONLY — outcomes NOT included, "
        f"do NOT invent surprise direction):\n{_fmt(past_events)}"
        if past_events
        else "past_macro_events_last_14d: (none)"
    )
    upcoming_block = (
        f"upcoming_macro_events_next_14d (calendar, NAMES ONLY — no outcomes):\n{_fmt(upcoming_events)}"
        if upcoming_events
        else "upcoming_macro_events_next_14d: (none)"
    )
    ev_block = f"{past_block}\n\n{upcoming_block}"

    if macro_news:
        news_lines = ["live_macro_news (SPY-proxy headlines, newest first):"]
        for n in macro_news:
            ts = n.get("published_at") or "?"
            title = (n.get("title") or "").strip()
            publisher = n.get("publisher") or ""
            if title:
                news_lines.append(f"  - [{ts}] {publisher}: {title}")
        news_block = "\n".join(news_lines)
    else:
        news_block = (
            "live_macro_news: (none available — restrict yourself to calendar + caveats, "
            "do NOT invent specific event outcomes)"
        )

    return (
        f"asset: {symbol} @ {venue}\n"
        f"market_type: {market_type}\n"
        f"as_of: {as_of.isoformat()}\n\n"
        f"{ev_block}\n\n"
        f"{news_block}\n\n"
        f"**If live_macro_news has headlines, anchor your read on them** "
        f"(macro tone, theme repetition, surprises mentioned).\n"
        f"**If no live_macro_news**, do NOT fabricate specific outcomes "
        f"(e.g. 'CPI surprised upside'); use conditional language and cap confidence ≤ 0.5.\n\n"
        f"Output the required JSON only."
    )
