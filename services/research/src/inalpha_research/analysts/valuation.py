"""Valuation analyst —— 相对估值视角（D-10）。

分析框架借鉴 anthropics/financial-services 的 ``comps-analysis`` skill（Apache-2.0）：
**可比公司 / 相对估值**——按行业选合适倍数（PE / PB / EV-EBITDA 等），把标的的
倍数对比 peer set / 历史区间 / 行业中位，识别 over / under-valued 离群。借的是
**分析框架**，不是该 skill 的 Excel 建模 / 数据源优先级部分（那部分 Inalpha 用不上）。

**与 fundamental analyst 的分工**：fundamental 看"标的叙事 / 周期 / 结构驱动"；
valuation 专看"现在的价格相对其基本面指标贵不贵"，是一个独立的估值视角。

**关键硬约束（呼应 D-9 freshness / truthfulness 纪律，与 fundamental.py 一致）**：

- Inalpha **没有完整三表 / 现金流预测 / analyst consensus feed**——只有
  ``data.get_fundamentals`` 的免费快照指标（PE / PB / ROE / margins 等）。
- 因此**只做相对估值（comps 风格），禁止展开完整 DCF**：DCF 需要多年现金流预测，
  数据拿不到会逼 LLM 编数，违反 freshness 硬约束。无现金流数据时退化为"基于现有
  指标的相对估值"，并显式说明数据局限。
- 不编造 peer 具体倍数 / 历史区间数值；缺数据时降 confidence（无 live 数据 cap 0.55）、
  用 relative phrasing，绝不把训练期记忆当现在。
"""
from __future__ import annotations

from datetime import datetime

from ..researchers.base import infer_asset_type
from .base import Analyst

_SYSTEM = """
You are a relative valuation analyst covering ANY asset class. Your single job:
judge whether the asset looks **over- / fairly- / under-valued** RELATIVE to its own
fundamentals, sector peers, and historical range — borrowing the comparable-company
(comps) framework. The user prompt gives you ``market_type``; pick the right multiples
from the table below before writing.

**HARD CONSTRAINT — DATA TRUTHFULNESS (D-9/D-10)**:

The system has **NO full financial statements, NO multi-year cash-flow data, NO analyst
consensus feed**. It only passes you a snapshot of free fundamental indicators
(PE / PB / ROE / margins / market cap) plus optional web-search blurbs. You must NOT:
- Run a **full DCF** (multi-year FCF projections + terminal value). You lack the inputs;
  attempting it forces fabrication. If asked to "value" the asset, do **relative
  valuation only** and say so.
- Quote specific peer multiples, sector medians, or historical multiple ranges as hard
  numbers unless they appear in the user prompt — your training-time memory of these is
  stale relative to ``as_of``.
- Invent EPS / revenue / growth figures not in the prompt.

You may:
- Reason about the **provided** multiples relative to **qualitative** sector norms
  ("a PE of X for a mature bank is on the high side; for a high-growth name it is not").
- Use range / regime language and lower confidence (cap at **0.55** without live peer
  data) and say so in the summary.

| market_type    | Relative-valuation anchors                                                  |
|----------------|------------------------------------------------------------------------------|
| crypto         | NVT / MVRV / realized-cap vs market-cap, supply schedule, fee revenue       |
| us_stock       | PE / forward PE, EV/EBITDA, PB, FCF yield, PEG vs growth                     |
| cn_stock       | PE / PB vs 行业 / 历史分位, ROE, 股息率, 政策溢价 / 折价                       |
| hk_stock       | PE / PB, A/H 价差, 股息率, Southbound 溢价                                    |
| global_stock   | local PE / PB vs regional peers, FX-adjusted, sector multiple norms          |

You do NOT do price chart / momentum analysis (the technical analyst handles that).

Return ONLY a JSON object with this exact shape:

{
  "stance": "bullish" | "bearish" | "neutral",   // bullish = under-valued, bearish = over-valued
  "confidence": float in [0, 1],
  "summary": "1-2 sentence valuation verdict (cheap / fair / expensive + why)",
  "key_points": ["bullet 1", "bullet 2", ...],   // up to 5 items
  "factors": [                                    // 1-3 valuation factors
    {
      "name": "pe_vs_sector",                     // snake_case identifier
      "kind": "macro",                            // valuation factors → encode as "macro"
      "value": "rich" | "cheap" | 0.42,           // string label or number
      "strength": 0.5,                            // 0-1
      "horizon": "position",                      // valuation is a position-horizon signal
      "explanation": "PE elevated vs the asset's own ROE / growth profile"
    }
  ]
}

Rules for factors:
- Output 1-3 factors. ``kind`` MUST be "macro" (the factor schema has no dedicated
  "valuation" kind yet; encode valuation drivers as macro).
- ``horizon`` is normally "position" (valuation re-rates slowly).
- If you lack live peer / historical data, lower ``strength`` and ``confidence``; do not
  invent multiples.
""".strip()


class ValuationAnalyst(Analyst):
    """相对估值 analyst（5 类资产多市场感知，comps 框架）。"""

    type_id = "valuation"

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

        # 复用免费 fundamentals 快照（PE / PB / ROE / margins）作相对估值的锚
        financials = await self._data.get_fundamentals(venue=venue, symbol=symbol)
        financials_block = _render_valuation_inputs(financials)

        # web search 补充（peer / 行业估值的定性背景；不当硬数字用）
        web_block = ""
        web_results = await self._data.get_web_search(
            f"{symbol} 估值 市盈率 行业对比 valuation 2026", max_results=3
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
            "**IMPORTANT TIME / DATA DISCIPLINE**:\n"
            "- `as_of` above is the TRUE current time of this research.\n"
            "- Do **relative valuation only** — NO full DCF (you lack cash-flow inputs).\n"
            "- Anchor on the REAL indicators above; do NOT fabricate peer multiples or\n"
            "  historical ranges. If they're absent, reason qualitatively and lower\n"
            "  confidence.\n"
            "- If indicators are missing / stale, say so in the summary and cap confidence\n"
            "  at 0.55.\n\n"
            "Output the required JSON only."
        )


def _render_valuation_inputs(data: dict) -> str:
    """把 fundamentals 快照里**与估值相关**的指标 format 给 LLM。"""
    if not data.get("available"):
        return (
            f"valuation_inputs: (not available — {data.get('reason', 'unknown')})\n"
            "No live multiples — do qualitative relative valuation only, cap confidence 0.55."
        )
    ind = data.get("indicators", {})
    lines = ["valuation_inputs (most recent disclosure):"]
    # 估值相关指标优先（倍数 + 盈利质量），增长 / 杠杆作辅助
    labels = {
        "market_cap": "市值",
        "pe_ratio": "市盈率 PE",
        "pb_ratio": "市净率 PB",
        "roe": "ROE",
        "gross_margin": "毛利率",
        "net_margin": "净利率",
        "revenue_yoy": "营收同比",
        "profit_yoy": "利润同比",
        "debt_to_equity": "负债权益比",
    }
    pct_keys = {"roe", "revenue_yoy", "profit_yoy", "gross_margin", "net_margin", "debt_to_equity"}
    for key, label in labels.items():
        val = ind.get(key)
        if val is None:
            continue
        if key in pct_keys:
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
        "Judge cheap / fair / expensive RELATIVE to these indicators + qualitative sector "
        "norms. Do NOT invent peer multiples. If PE / PB are missing, lower confidence."
    )
    return "\n".join(lines)


def _render_web_results(results: list[dict]) -> str:
    """web search 结果（定性背景，不当硬数字）。"""
    if not results:
        return ""
    lines = ["web_search_results (qualitative context only, NOT hard numbers):"]
    for r in results[:3]:
        title = r.get("title", "")[:100]
        snippet = r.get("snippet", "")[:200]
        lines.append(f"  - {title}")
        if snippet:
            lines.append(f"    {snippet}")
    return "\n".join(lines) + "\n"
