"""因子源适配器协议 + 因子定义。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

import pandas as pd


@dataclass(frozen=True, slots=True)
class FactorSpec:
    """单个因子的静态定义（进 catalog）。"""

    factor_id: str
    source: str
    name: str
    kind: str  # momentum | mean_reversion | volatility | volume | trend
    needs_universe: bool = False
    direction_hint: int = 0  # +1/-1/0 先验；真实方向以 rank_ic 符号为准
    extras: dict[str, str] = field(default_factory=dict)


@runtime_checkable
class FactorAdapter(Protocol):
    """因子源统一协议。"""

    source: str

    def available(self) -> bool:
        """该源是否可用（库已装 / 已启用）。不可用时 specs() 仍可列出但 compute 跳过。"""
        ...

    def specs(self) -> list[FactorSpec]:
        """列出本源提供的因子定义。"""
        ...

    def compute(
        self, df: pd.DataFrame, factor_ids: list[str] | None = None
    ) -> dict[str, pd.Series]:
        """从 OHLCV 算因子时序。

        Args:
            df: index = tz-aware ts（升序），列含 open/high/low/close/volume。
            factor_ids: 只算这些；None = 算本源全部可时序计算的因子。

        Returns:
            factor_id -> 与 df.index 对齐的 Series（warmup 段为 NaN）。
            不可用 / 横截面因子返回空 dict 或跳过对应 id。
        """
        ...
