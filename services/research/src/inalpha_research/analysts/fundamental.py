"""Fundamental analyst —— 标的自身叙事 / 周期判断（D-8b: LLM-only，无外部数据）。

D-9 起：system prompt 升级为**多市场感知**——同一个 analyst 在 crypto / 美股 / A股 /
港股 / 全球股市 5 类资产上自动切术语（halving vs 10-K vs 年报）。market_type 由
``researchers.base.infer_asset_type`` 推断后塞进 user prompt。

后续真接外部数据时（D-9+），把 ``self._data.get_*`` / FRED / SEC EDGAR 拉来的事实
追加进 user prompt 即可，system 不变。
"""
from __future__ import annotations

from datetime import datetime

from ..researchers.base import infer_asset_type
from .base import Analyst

_SYSTEM = """
You are a fundamental / macro analyst covering ANY asset class. The user prompt
tells you the ``market_type`` (crypto / us_stock / cn_stock / hk_stock / global_stock);
pick the right analytical framework from the table below before writing.

**HARD CONSTRAINT — DATA TRUTHFULNESS (D-9)**:

The system currently has **NO live fundamentals feed** (no SEC filings, no
earnings transcripts, no on-chain real-time data, no analyst consensus pull).
You must NOT:
- Quote specific past forecasts as if still valid ("DRAM downturn lasts until
  mid-2025", "iPhone 16 cycle peaks Q2 2025", "BTC ETF flows hit X this week") —
  these are **training-time data points**, almost certainly stale relative to as_of.
- Cite EPS numbers, revenue figures, margin percentages, or product roadmap
  specifics unless they appear in the user prompt.
- Treat your training knowledge of "the most recent earnings cycle" as current —
  multiple quarters have likely passed.

You may:
- Discuss **structural drivers** in relative terms ("memory chip cycles tend
  to last 6-18 months; we appear N quarters in but the exact phase is unknown").
- Use **range / regime** language ("foundry demand has been a structural tailwind
  in recent years; whether that's still expanding is unknown without live data").
- Lower confidence (cap at **0.55** without live data) and say so in summary.

| market_type    | What to anchor on                                                          |
|----------------|----------------------------------------------------------------------------|
| crypto         | on-chain flows, supply schedule / halving, exchange reserves, ETF / RWA   |
| us_stock       | 10-K / 10-Q segment revenue, EPS, guidance, FCF, buyback / dilution        |
| cn_stock       | 年报 / 季报 ROE / 毛利率, 行业政策, 北向资金, 供应链外汇敞口                   |
| hk_stock       | interim / annual report, Southbound flow, A/H 价差, 监管 / HKD-rate         |
| global_stock   | local annual / interim, FX exposure, regional regulatory / policy           |

You do NOT use price chart analysis (the technical analyst handles that).

Return ONLY a JSON object with this exact shape:

{
  "stance": "bullish" | "bearish" | "neutral",
  "confidence": float in [0, 1],
  "summary": "1-2 sentence core thesis",
  "key_points": ["bullet 1", "bullet 2", ...],   // up to 5 items
  "factors": [                                    // 1-3 fundamental / macro factors
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
- Output 1-3 factors. Each must be a real fundamental / macro / regulatory driver —
  not invented prices or events.
- "kind" should be "macro" for monetary / regulation / structural; "sentiment" for
  adoption / narrative.
- If you lack any specific recent data, lower the strength, do not invent.
- For non-crypto market_types, factor.kind is still macro / sentiment (factor schema
  does not yet have "earnings" / "policy" sub-kinds; encode them as macro).
- Confidence and factor.strength should reflect data freshness.
""".strip()


class FundamentalAnalyst(Analyst):
    """基本面 analyst（5 类资产多市场感知）。"""

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
        market_type = infer_asset_type(venue=venue, symbol=symbol)

        # Fetch real financial data when available
        financials = await self._data.get_fundamentals(venue=venue, symbol=symbol)
        financials_block = _render_financials(financials, market_type)

        # Always try web search as supplementary data source (all markets)
        # Crypto: fills the gap when yfinance returns limited/no financials data
        web_block = ""
        ticker_name = symbol
        # 年份取 as_of 动态拼（issue #63），不写死——跨年后硬编码年份=要陈旧财报当最新
        web_results = await self._data.get_web_search(
            f"{ticker_name} 最新财报 营收 利润 {as_of.year}", max_results=3
        )
        if web_results:
            web_block = _render_web_results(web_results)

        return (
            f"asset: {symbol} @ {venue}\n"
            f"market_type: {market_type}\n"
            f"as_of: {as_of.isoformat()}  ← THIS IS NOW (current research time)\n"
            f"window_days: {lookback_days}\n\n"
            f"{financials_block}\n"
            f"{web_block}\n"
            "**IMPORTANT TIME DISCIPLINE**:\n"
            "- `as_of` above is the TRUE current time of this research.\n"
            "- Your training cutoff is likely earlier than `as_of`.\n"
            "- **Do NOT** state outdated specific forecasts as if they still apply\n"
            "  (e.g. avoid claims like 'DRAM downturn lasts until mid-2025' when "
            "as_of is 2026+).\n"
            "- When you reference past-period data (earnings / cycle phase / policy),\n"
            "  use **relative phrasing** ('the most recent earnings cycle showed...',\n"
            "  '~12-18 months into the current cycle...').\n"
            "- If your knowledge of post-cutoff developments is thin, **lower confidence**\n"
            "  and say so in summary; do not fabricate.\n\n"
            "Output the required JSON only."
        )


def _render_financials(data: dict, market_type: str) -> str:
    """Format financial data for LLM consumption."""
    if not data.get("available"):
        return (
            f"financial_data: (not available — {data.get('reason', 'unknown')})\n"
            f"Use training knowledge with lower confidence (cap 0.55)."
        )
    ind = data.get("indicators", {})
    lines = ["financial_data (most recent disclosure):"]
    labels = {
        "market_cap": "市值",
        "pe_ratio": "市盈率",
        "pb_ratio": "市净率",
        "roe": "ROE",
        "revenue_yoy": "营收同比",
        "profit_yoy": "利润同比",
        "gross_margin": "毛利率",
        "net_margin": "净利率",
        "debt_to_equity": "负债权益比",
    }
    for key, label in labels.items():
        val = ind.get(key)
        if val is not None:
            if key in (
                "roe",
                "revenue_yoy",
                "profit_yoy",
                "gross_margin",
                "net_margin",
                "debt_to_equity",
            ):
                lines.append(f"  {label}: {val * 100:.1f}%")
            elif key == "market_cap":
                if val > 1e12:
                    lines.append(f"  {label}: {val / 1e12:.1f}万亿")
                elif val > 1e8:
                    lines.append(f"  {label}: {val / 1e8:.1f}亿")
                else:
                    lines.append(f"  {label}: {val:.0f}")
            else:
                lines.append(f"  {label}: {val:.2f}")
    lines.append("")
    lines.append(
        "Anchor your analysis on the above REAL data. Do NOT fabricate numbers."
    )
    lines.append(
        "If data seems stale or incomplete, note it and lower confidence accordingly."
    )
    return "\n".join(lines)


def _render_web_results(results: list[dict]) -> str:
    """Format web search results for LLM consumption."""
    if not results:
        return ""
    lines = ["web_search_results (latest):"]
    for r in results[:3]:
        title = r.get("title", "")[:100]
        snippet = r.get("snippet", "")[:200]
        lines.append(f"  - {title}")
        if snippet:
            lines.append(f"    {snippet}")
    return "\n".join(lines) + "\n"
