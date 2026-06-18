"""PersonaAnalyst —— 投资大师人格 analyst 的共享基类（ADR-0037 §A）。

与普通 analyst 的区别：persona 不是"某个维度的客观分析"，而是"某位投资大师的
**风格化视角**"——同一份数据，Buffett 看护城河、Wood 看颠覆曲线、Burry 看泡沫。
多个 persona 进同一条 deep_dive，天然形成"大师团"的多视角 / 对立观点，喂给
Bull/Bear 辩论与 manager 综合。

设计（呼应 ``valuation.py`` D-10 模式，避免重复造轮子）：

- ``build_user_prompt`` 在本基类**实现一次**：market_type 推断 + 免费 fundamentals
  快照 + 可选 web 搜索（按 persona 的 ``search_focus`` 聚焦）+ freshness footer。
- 每个具体 persona 只需提供 ``type_id`` + ``search_focus`` + ``system_prompt()``
  （一段稳定的风格化 _SYSTEM，cache 友好，ADR-0014）。
- 输出契约（多市场措辞表 + truthfulness 硬约束 + JSON shape + factor 规则）由
  ``PERSONA_OUTPUT_CONTRACT`` 统一拼到每个 _SYSTEM 末尾，保证 6 个 persona 行为一致。

**硬约束（CLAUDE.md §3.1 / §3.2，与 valuation.py 一致）**：

- ``as_of`` 是真现在；persona 禁把训练期对某标的的看法当现在的结论。
- 缺 live 数据时降 confidence（cap 0.55）并显式说明，绝不编造倍数 / 财务数字。
- persona lens 不适配资产时（如 Buffett 看 memecoin、Wood 看公用事业）显式降
  confidence 并说明"此标的不在我的能力圈"，不强行套用。
- factor ``kind`` 只能用 ``FactorKind`` 现有值；persona 的定性判断统一编码为
  ``"macro"``（与 valuation 一致，schema 暂无 persona / valuation 专属 kind）。
"""
from __future__ import annotations

from datetime import datetime

from ...researchers.base import infer_asset_type
from ..base import Analyst
from ..utils import fundamentals_route, render_financial_indicators, render_web_results

#: 所有 persona 共享的输出契约 —— 拼到每个具体 persona 的风格化 lens 之后。
#: 放在基类集中维护，保证 6 个 persona 的多市场措辞 / 纪律 / JSON shape 完全一致。
PERSONA_OUTPUT_CONTRACT = """
You cover ANY asset class. The user prompt gives you ``market_type``; pick the right
vocabulary before writing (do NOT talk P/E on a memecoin or halving on an A-share):

| market_type    | What to anchor your lens on                                          |
|----------------|----------------------------------------------------------------------|
| crypto         | network adoption, tokenomics / supply schedule, real usage & fees, narrative durability |
| us_stock       | moat / brand, margins, free cash flow, capital allocation, growth-vs-multiple |
| cn_stock       | 政策 / 行业格局, ROE, 国企 / 民企属性, 估值历史分位                       |
| hk_stock       | 行业格局, 南向资金, A/H 溢价, 股息率, 跨境监管                            |
| global_stock   | local market dominance, regulatory barriers, FX exposure, sector norms |

**HARD CONSTRAINT — DATA TRUTHFULNESS (CLAUDE.md §3.1 / §3.2)**:

- ``as_of`` in the user prompt is the TRUE current time. Your training cutoff is earlier.
  Do NOT present a training-time opinion about this specific asset as a current fact, and
  do NOT cite specific dates (earnings, FOMC, halvings) unless they appear in the prompt.
- You only get a snapshot of free fundamental indicators (PE / PB / ROE / margins / market
  cap) plus optional web blurbs. Do NOT invent peer multiples, growth figures, or financial
  statements not in the prompt. When the data is missing, reason qualitatively, say so, and
  cap confidence at **0.55**.
- This asset may not fit your style (e.g. a deep-value lens on a pre-revenue token). If it's
  outside your circle of competence, say so plainly and LOWER confidence — do not force the
  thesis.

Return ONLY a JSON object with this exact shape:

{
  "stance": "bullish" | "bearish" | "neutral",
  "confidence": float in [0, 1],
  "summary": "1-2 sentence verdict in YOUR voice (what your style concludes + why)",
  "key_points": ["bullet 1", "bullet 2", ...],   // up to 5, in your style's language
  "factors": [                                    // 1-3 factors your style cares about
    {
      "name": "moat_width",                       // snake_case identifier
      "kind": "macro",                            // persona factors → encode as "macro"
      "value": "wide" | "narrow" | 0.7,           // string label or number
      "strength": 0.6,                            // 0-1, how much it drives your view
      "horizon": "position",                      // most persona views are position-horizon
      "explanation": "1 short sentence"
    }
  ]
}

Rules for factors:
- Output 1-3 factors. ``kind`` MUST be "macro" (schema has no persona kind yet).
- ``horizon`` is normally "position" (style-driven views re-rate slowly), except for
  flow / trend driven styles which may use "swing".
- Lacking live data → lower ``strength`` and ``confidence``; never fabricate numbers.
""".strip()


def build_persona_system(lens: str) -> str:
    """把 persona 风格化 lens 与共享输出契约拼成完整 system prompt。

    ``lens`` 必须以 ``You are <Name>`` 开头（FakeLLMClient 按 system 子串匹配，测试靠
    这个锚定词命中预设；同时也是给真 LLM 的角色锚）。
    """
    return f"{lens.strip()}\n\n{PERSONA_OUTPUT_CONTRACT}"


class PersonaAnalyst(Analyst):
    """投资大师人格 analyst 的抽象基类。

    子类只需提供 ``type_id`` / ``search_focus`` / ``system_prompt()``。本类统一实现
    ``build_user_prompt``（与 ``valuation.py`` 同构）。
    """

    #: 落进 ``AnalystBrief.analyst``，如 ``"persona_buffett"``。子类必须 override。
    type_id: str = ""

    #: web 搜索聚焦词（该 persona 关注的角度），拼进 query；空字符串 = 不搜。
    search_focus: str = ""

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

        # 免费 fundamentals 快照（与 valuation 共用同一数据源 + 同一 venue 路由；
        # crypto 直接跳过请求，显式 unavailable）
        fund_venue = fundamentals_route(venue=venue, market_type=market_type)
        if fund_venue is None:
            financials = {"available": False, "reason": "crypto has no financial statements"}
        else:
            financials = await self._data.get_fundamentals(venue=fund_venue, symbol=symbol, as_of=as_of)
        financials_block = _render_fundamentals(financials)

        # 双档 confidence cap（run() 里代码级 clamp）：与 fundamental/valuation 同纪律——
        # 无 live 财报（crypto / 拉取失败）时大师人格也不该过度自信（CR #86）。
        self._confidence_cap = 0.75 if financials.get("available") else 0.55

        # 按 persona 关注点做 web 搜索（定性背景，不当硬数字用）
        web_block = ""
        if self.search_focus:
            web_results = await self._data.get_web_search(
                f"{symbol} {self.search_focus} {as_of.year}", max_results=3
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
            "**TIME / DATA DISCIPLINE**:\n"
            "- `as_of` above is the TRUE current time of this research.\n"
            "- Anchor on the REAL indicators above; do NOT fabricate peer multiples, growth\n"
            "  figures, or events not shown. If they're absent, reason qualitatively and\n"
            "  lower confidence (cap 0.55).\n"
            "- If this asset is outside your style's circle of competence, say so and lower\n"
            "  confidence — do not force the thesis.\n\n"
            "Give your verdict in YOUR voice. Output the required JSON only."
        )


def _render_fundamentals(data: dict) -> str:
    """把 fundamentals 快照里与"生意质量 / 估值"相关的指标 format 给 LLM（共用 utils 渲染）。

    与 ``valuation._render_valuation_inputs`` 共享同一套 labels / 格式化，仅标题与末尾
    指引不同（这里偏"质量 / 安全边际"维度，服务 persona 风格判断）。
    """
    return render_financial_indicators(
        data,
        label="fundamentals",
        unavailable_hint=(
            "No live indicators — judge qualitatively only and cap confidence at 0.55."
        ),
        footer="Judge through YOUR style's lens. Do NOT invent figures beyond these.",
    )
