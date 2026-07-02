"""Analyst 集合：单视角研究员。

D-8c 起 5 个 analyst；D-10 加 valuation 共 6 个：

- ``technical``   ：吃 K 线 + 简单指标（SMA / RSI），短期立场
- ``fundamental`` ：标的自身叙事（halving / ETF flows / on-chain），纯 LLM
- ``sentiment``   ：Fear & Greed Index 反向推理，外部 API
- ``risk``        ：波动率 / 回撤，给 manager 一票否决信号
- ``macro``       ：宏观环境（FOMC / CPI / DXY）+ 近期日程
- ``valuation``   ：相对估值（comps 框架，借鉴 anthropics/financial-services，Apache-2.0），
  吃免费 fundamentals 快照判贵贱

新增 analyst 加进 ``ALL_ANALYSTS`` 自动并行；schema 端只需在 ``AnalystBrief.analyst``
Literal 里加值。
"""
from .base import Analyst, AnalystContext
from .fundamental import FundamentalAnalyst
from .macro import MacroAnalyst
from .risk import RiskAnalyst
from .sentiment import SentimentAnalyst
from .technical import TechnicalAnalyst
from .valuation import ValuationAnalyst

ALL_ANALYSTS: list[type[Analyst]] = [
    TechnicalAnalyst,
    FundamentalAnalyst,
    SentimentAnalyst,
    RiskAnalyst,
    MacroAnalyst,
    ValuationAnalyst,
]

__all__ = [
    "ALL_ANALYSTS",
    "Analyst",
    "FundamentalAnalyst",
    "MacroAnalyst",
    "RiskAnalyst",
    "SentimentAnalyst",
    "TechnicalAnalyst",
    "ValuationAnalyst",
]
