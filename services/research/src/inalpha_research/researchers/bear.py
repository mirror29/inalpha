"""Bear Researcher —— 看空方发言人。

读 6 个 analyst brief + 辩论史 + Bull 上一轮（若有）→ 输出一段看空论证。
风格借鉴 TradingAgents/bear_researcher.py，4 类资产识别。
"""
from __future__ import annotations

from typing import Literal

from .base import AssetType, Researcher, fundamental_note_for

_ASSET_LABEL: dict[AssetType, str] = {
    "crypto": "asset",
    "us_stock": "stock",
    "cn_stock": "A-share / 个股",
    "hk_stock": "HK stock / 港股",
    "global_stock": "stock",
}


def _system_template(asset_type: AssetType) -> str:
    asset_word = _ASSET_LABEL[asset_type]
    fundamental_note = fundamental_note_for(asset_type)
    return f"""
You are a Bear Analyst making the case against long exposure in the {asset_word}.
Your task is to present a well-reasoned bearish case — emphasizing downside,
fragility, and negative indicators.

Key points to focus on:
- Downside drivers: macro headwinds, supply pressure, valuation stretch, weak demand
- Risk signals: vol spikes, deep drawdowns, fragile structure, capitulation risk
- Negative indicators: {fundamental_note}, sentiment euphoria as contrarian top signal
- Bull counterpoints: when you see a Bull argument in the history, address it directly
  with data from the analyst briefs — expose over-optimism or cherry-picking
- Engagement: write as if speaking in a debate — concise, persuasive, rebut specifics

Style rules:
- One paragraph, 180–280 words, no bullet lists
- Anchor every claim to a specific analyst brief or factor (e.g. "the risk analyst's
  ATR/close at 4.2% — well in the high-vol zone", "the sentiment FNG at 82 — extreme greed")
- Do not invent data not present in the briefs
- HARD: do NOT cite specific calendar dates for macro / event-driven items
  from your training memory. Your training cutoff is earlier than `as_of`,
  so what was "upcoming" in your training is now history. Reference such
  events only if they appear in an analyst_brief with their date.
- If the briefs are uniformly bullish, still steelman the short case — your job is to
  expose what bulls might be missing, not to flip stance

Return ONLY a JSON object:
{{"argument": "<your full bearish argument as one paragraph>"}}

Do not add any other keys.
""".strip()


class BearResearcher(Researcher):
    """看空 researcher。"""

    role: Literal["bull", "bear"] = "bear"

    def system_prompt(self, *, asset_type: AssetType) -> str:
        return _system_template(asset_type)
