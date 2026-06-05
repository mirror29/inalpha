"""Inalpha factor service。

接市面现成因子库（pandas-ta / WorldQuant Alpha101 / qlib Alpha158）+ 自实现的
"有效性"打分（前瞻收益分位 + 时序 Rank IC），让 agent 与研究 analyst 能引用
**经验证有效的因子**做分析与择时。

详见 ``docs/miro/11-factor-library-integration.md``。
"""
from __future__ import annotations

__version__ = "0.1.0"
