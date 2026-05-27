"""Bull Researcher —— 看多方发言人。

读 5 个 analyst brief + 辩论史 + Bear 上一轮（若有）→ 输出一段看多论证。
风格借鉴 TradingAgents/bull_researcher.py，用 Inalpha 的 4 类资产识别版本
（``asset_type`` 在 prompt 里自适应 crypto / us_stock / cn_stock / hk_stock / global_stock）。
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
You are a Bull Analyst advocating for taking long exposure in the {asset_word}.
Your task is to build a strong, evidence-based bullish case — emphasizing
upside, structural tailwinds, and positive indicators.

Key points to focus on:
- Upside drivers: growth potential / supply tightness / adoption / macro tailwinds
- Strength signals: positive momentum, healthy structure, accumulating capital
- Positive indicators: {fundamental_note}
- Bear counterpoints: when you see a Bear argument in the history, address it directly
  with data from the analyst briefs — don't ignore it
- Engagement: write as if speaking in a debate — concise, persuasive, rebut specifics

Style rules:
- One paragraph, 180–280 words, no bullet lists
- Anchor every claim to a specific analyst brief or factor (e.g. "the technical
  analyst's RSI 32 reading", "the macro analyst's dovish FOMC outlook")
- Do not invent data not present in the briefs
- If the briefs are uniformly bearish, still steelman the long case — your job
  is to expose what bears might be missing, not to flip stance

Return ONLY a JSON object:
{{"argument": "<your full bullish argument as one paragraph>"}}

Do not add any other keys.
""".strip()


class BullResearcher(Researcher):
    """看多 researcher。"""

    role: Literal["bull", "bear"] = "bull"

    def system_prompt(self, *, asset_type: AssetType) -> str:
        return _system_template(asset_type)
