"""因子源适配器。

每个适配器把一套现成因子库包装成统一协议 ``FactorAdapter``：暴露因子定义
（``specs()``）+ 从 OHLCV DataFrame 算因子时序（``compute()``）。

- ``pandas_ta_adapter`` —— pandas-ta 技术指标（库不可用时用纯 pandas 兜底，永远可用）
- ``alpha101_adapter``  —— WorldQuant 101 alpha 的可时序子集（纯 pandas）
- ``qlib_alpha_adapter`` —— qlib Alpha158 风格因子（FACTOR_QLIB_ENABLED 开关 + import 守卫）
"""
from __future__ import annotations

from .alpha101_adapter import Alpha101Adapter
from .base import FactorAdapter, FactorSpec
from .pandas_ta_adapter import PandasTAAdapter
from .qlib_alpha_adapter import QlibAlphaAdapter

__all__ = [
    "Alpha101Adapter",
    "FactorAdapter",
    "FactorSpec",
    "PandasTAAdapter",
    "QlibAlphaAdapter",
]
