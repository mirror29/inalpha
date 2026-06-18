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
from .utils import fundamentals_route, render_financial_indicators, render_web_results

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
- Use range / regime language. Two-tier confidence (D-12): when the prompt contains a
  ``valuation_inputs (most recent disclosure ...)`` block with real indicators, your
  confidence may go up to **0.75**; when it says ``(not available ...)``, cap at
  **0.55** and say so in the summary.

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

        # 复用免费 fundamentals 快照（PE / PB / ROE / margins）作相对估值的锚；
        # venue 经 fundamentals_route 路由（alpaca/binance 直透 422 → 永远 unavailable）
        fund_venue = fundamentals_route(venue=venue, market_type=market_type)
        if fund_venue is None:
            financials = {
                "available": False,
                "reason": "crypto has no financial statements — use NVT/MVRV-style reasoning",
            }
        else:
            financials = await self._data.get_fundamentals(venue=fund_venue, symbol=symbol, as_of=as_of)
        financials_block = _render_valuation_inputs(financials)

        # 双档 confidence cap（run() 里代码级 clamp）：有 live 指标 0.75，无 0.55
        self._confidence_cap = 0.75 if financials.get("available") else 0.55

        # web search 补充（peer / 行业估值的定性背景；不当硬数字用）
        web_block = ""
        # 英文 query：面向全球资产（US/JP/EU/A股…）的中性检索；中文 query 会让
        # ddgs 偏向中文/A股视角结果（CLAUDE.md §3「面向全球用户」）。
        web_results = await self._data.get_web_search(
            f"{symbol} valuation PE PB ratio peer comparison {as_of.year}", max_results=3
        )
        if web_results:
            web_block = render_web_results(web_results)

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
    """把 fundamentals 快照里**与估值相关**的指标 format 给 LLM（共用 utils 渲染）。"""
    return render_financial_indicators(
        data,
        label="valuation_inputs",
        unavailable_hint=(
            "No live multiples — do qualitative relative valuation only, cap confidence 0.55."
        ),
        footer=(
            "Judge cheap / fair / expensive RELATIVE to these indicators + qualitative sector "
            "norms. Do NOT invent peer multiples. If PE / PB are missing, lower confidence."
        ),
    )
