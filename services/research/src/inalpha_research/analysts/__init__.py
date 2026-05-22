"""Analyst 集合：单视角研究员。

D-8b 起 2 个 analyst：

- ``technical``：技术分析，吃 K 线 + 简单指标，输出短期立场
- ``fundamental``：基本面 / 宏观叙事，纯 LLM（无外部数据），输出中长期立场

未来扩展：``sentiment``（社媒）/ ``news``（新闻），结构相同，加进 ``ALL_ANALYSTS``。
"""
from .base import Analyst
from .fundamental import FundamentalAnalyst
from .technical import TechnicalAnalyst

ALL_ANALYSTS: list[type[Analyst]] = [TechnicalAnalyst, FundamentalAnalyst]

__all__ = ["ALL_ANALYSTS", "Analyst", "FundamentalAnalyst", "TechnicalAnalyst"]
