"""Macro analyst —— 宏观环境 + 近期日程（multi-market 感知）。

D-9 起：

- **跟 fundamental 的区分**：
  - ``fundamental`` 关注**该标的**自身（halving / ETF flows / 财报 / 政策）
  - ``macro``      关注**宏观环境**（Fed rate / DXY / 地缘 / 当周日程）+ 不同市场的传导
- 数据：硬编码 `_MACRO_CALENDAR`（近期高影响事件列表，以美国为主）
- LLM 拿 ``as_of ± 14 天`` 范围内的事件 + market_type → 输出按市场传导调整的 stance

不同市场对同一 macro 事件的反应不同（FOMC 鹰派对美股直接打、对 crypto 高敏感、
对 A 股次级影响、港股因 USD-peg 直传）—— 由 LLM 在 system prompt 的传导表内自适应。

D-12 起：接 FRED live 读数——直接走 data 服务 ``venue=fred`` 拉 5 条 daily 序列
（DFF / DGS10 / DGS2 / DTWEXBGS / VIXCLS，与 factor 服务 macro_adapter 同一来源），
本地算 level / Δ20obs / 期限利差 / 美元动量，按 +1 天发布滞后做 point-in-time 截断。
不走 factor ``/compute``：它要求**该标的**的 1d bars 已在 DB（1h 研究时大概率缺），
而 FRED 序列与标的无关，直拉链路最短。双档 confidence：有 live 读数或新闻 cap 0.7，
全无 cap 0.5（``Analyst.run()`` 代码级 clamp）。
"""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

from ..researchers.base import infer_asset_type
from .base import Analyst

#: FRED daily 序列 → 展示名。与 factor 服务 macro_adapter ``_SERIES_META`` 的
#: daily 组保持一致（都是 +1 天发布滞后的市场化序列）。
_FRED_SERIES: dict[str, str] = {
    "DFF": "Fed Funds effective rate (%)",
    "DGS10": "10Y Treasury yield (%)",
    "DGS2": "2Y Treasury yield (%)",
    "DTWEXBGS": "Broad USD index",
    "VIXCLS": "VIX",
}

#: daily 序列的发布滞后（天）—— 对齐 macro_adapter 的 point-in-time 纪律：
#: as_of 时刻只能看到 observation date ≤ as_of - 1d 的观测。
_FRED_PUBLISH_LAG_DAYS = 1

#: 最近观测距 as_of 超过该天数标 stale 并提示降权。
_FRED_STALE_DAYS = 7

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

**HARD CONSTRAINT — DATA TRUTHFULNESS (D-9 / D-12 two-tier)**:

The calendar gives you **event NAMES + DATES only** (no outcomes, no realized
prints). The user prompt MAY additionally contain a ``live_macro_readings``
block — REAL, recently fetched FRED observations (Fed funds rate, Treasury
yields, curve slope, broad USD index, VIX) each tagged with its observation
date and staleness. When present, anchor your regime read on those numbers and
cite them as given (mind each line's staleness tag — discount stale ones).
When it says ``(none available ...)``, you have NO live feed.

Regardless, you MUST NOT:
- Claim specific directional outcomes you weren't told ("CPI surprised upside",
  "Fed cut by 25bps", "EUR/USD at 1.08") — unless the number appears verbatim
  in ``live_macro_readings`` / ``live_macro_news``, there is **no data for the
  claim**; you'd be hallucinating from training-time knowledge.
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

**Confidence ceiling (two-tier, D-12)**: when the user prompt contains
``live_macro_readings`` and/or ``live_macro_news`` with actual data, your
``confidence`` may go up to **0.7** (still: only cite numbers given verbatim).
When BOTH are ``(none available ...)``, cap your ``confidence`` at **0.5**.
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
        # D-12：FRED 读数与新闻并发拉（互不依赖，各自独立降级）。
        macro_news, readings = await asyncio.gather(
            self._data.get_news(symbol="SPY", limit=8),
            _fetch_macro_readings(self._data, as_of=as_of),
        )
        # 双档 cap（run() 里代码级 clamp）：有任一 live 数据 0.7，全无 0.5
        self._confidence_cap = 0.7 if (readings or macro_news) else 0.5
        return _format_user_prompt(
            venue=venue,
            symbol=symbol,
            as_of=as_of,
            events=events,
            market_type=market_type,
            macro_news=macro_news,
            readings=readings,
        )


async def _fetch_macro_readings(
    data: Any,
    *,
    as_of: datetime,
) -> dict[str, dict[str, Any]]:
    """并发拉 5 条 FRED daily 序列，本地算 point-in-time 读数。

    每条序列独立 try/except 降级（FRED key 缺失 / data 服务 4xx / 网络抖动都
    不应让 deep_dive 500——与 ``get_news`` 的吞错哲学一致）；全部失败返空 dict，
    caller 渲染 "(none available)" 走旧的 calendar-only 行为。

    Returns:
        ``{series_id: {"value", "chg_20obs", "obs_date", "staleness_days"}}``，
        只含成功拉到且通过 PIT 截断后非空的序列。
    """
    from_ts = as_of - timedelta(days=40)
    # PIT 纪律（对齐 factor macro_adapter）：daily 序列 +1 天发布滞后——
    # as_of 当天的观测在现实里还没发布，引用它就是未来函数。
    cutoff = as_of - timedelta(days=_FRED_PUBLISH_LAG_DAYS)

    async def _one(series_id: str) -> tuple[str, dict[str, Any]] | None:
        try:
            bars = await data.get_bars(
                venue="fred",
                symbol=series_id,
                timeframe="1d",
                from_ts=from_ts,
                to_ts=as_of,
                fresh=True,
            )
        except Exception:
            return None
        values: list[float] = []
        last_obs: datetime | None = None
        for b in bars:
            try:
                ts = datetime.fromisoformat(str(b["ts"]))
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=UTC)
                if ts > cutoff:
                    continue
                values.append(float(b["close"]))
                last_obs = ts
            except (KeyError, TypeError, ValueError):
                continue
        if not values or last_obs is None:
            return None
        reading: dict[str, Any] = {
            "value": values[-1],
            "obs_date": last_obs.date().isoformat(),
            "staleness_days": max(0, (as_of.date() - last_obs.date()).days),
        }
        if len(values) >= 21:
            reading["chg_20obs"] = values[-1] - values[-21]
        return series_id, reading

    results = await asyncio.gather(*(_one(s) for s in _FRED_SERIES))
    return {sid: r for item in results if item is not None for sid, r in [item]}


def _format_macro_readings(readings: dict[str, dict[str, Any]]) -> str:
    """FRED 读数 → ``live_macro_readings`` 块（含派生指标 + staleness 标注）。"""
    if not readings:
        return (
            "live_macro_readings: (none available — FRED feed unreachable or not "
            "configured; do NOT invent rate/USD/VIX levels)"
        )
    lines = [
        "live_macro_readings (FRED, point-in-time as of publish lag; "
        "each line shows its observation date):"
    ]

    def _fmt_line(sid: str) -> None:
        r = readings.get(sid)
        if r is None:
            return
        label = _FRED_SERIES[sid]
        chg = r.get("chg_20obs")
        chg_part = f", Δ20obs {chg:+.2f}" if chg is not None else ""
        stale_part = (
            f" [STALE: {r['staleness_days']}d old — discount accordingly]"
            if r["staleness_days"] > _FRED_STALE_DAYS
            else f" (obs {r['obs_date']}, {r['staleness_days']}d ago)"
        )
        lines.append(f"  - {label}: {r['value']:.2f}{chg_part}{stale_part}")

    for sid in _FRED_SERIES:
        _fmt_line(sid)

    # 派生指标：期限利差（经典衰退/周期信号，LLM 直接可用，免心算）
    dgs10, dgs2 = readings.get("DGS10"), readings.get("DGS2")
    if dgs10 and dgs2:
        slope = dgs10["value"] - dgs2["value"]
        lines.append(
            f"  - curve_slope (10Y-2Y): {slope:+.2f} "
            f"({'inverted' if slope < 0 else 'normal'})"
        )
    return "\n".join(lines)


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
    readings: dict[str, dict[str, Any]] | None = None,
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

    readings_block = _format_macro_readings(readings or {})

    return (
        f"asset: {symbol} @ {venue}\n"
        f"market_type: {market_type}\n"
        f"as_of: {as_of.isoformat()}\n\n"
        f"{ev_block}\n\n"
        f"{readings_block}\n\n"
        f"{news_block}\n\n"
        f"**If live_macro_readings has numbers, anchor your regime read on them** "
        f"(rate level/trend, curve slope, USD momentum, VIX regime) — mind each "
        f"line's staleness tag.\n"
        f"**If live_macro_news has headlines, anchor on them too** "
        f"(macro tone, theme repetition, surprises mentioned).\n"
        f"**If neither is available**, do NOT fabricate specific outcomes "
        f"(e.g. 'CPI surprised upside'); use conditional language and cap confidence ≤ 0.5.\n\n"
        f"Output the required JSON only."
    )
