"""用户策略实现 + 策略注册表。

D-7 起步 3 个示例：

- ``sma_cross``：经典快慢均线交叉
- ``buy_and_hold``：基准对照（第一根 bar 买入持有到结束）
- ``mean_reversion``：布林带均值回归 long-only

策略注册表给 ``POST /backtest`` 用 —— 通过 ``strategy_id`` 字符串路由到具体类。

后续添加策略：实现一个 ``Strategy`` 子类，在 ``_REGISTRY`` 注册一行即可。Phase F+ 接受
外部贡献时考虑 [ADR-0017 layer 1 沙盒](../../../../docs/decisions/0017-isolation-and-sandboxing.md)。
"""
from ..strategy.base import Strategy
from .buy_and_hold import BuyAndHoldStrategy
from .mean_reversion import MeanReversionStrategy
from .sma_cross import SMACrossStrategy

__all__ = [
    "BuyAndHoldStrategy",
    "MeanReversionStrategy",
    "SMACrossStrategy",
    "get_strategy_class",
    "list_strategies",
]


_REGISTRY: dict[str, type[Strategy]] = {
    "sma_cross": SMACrossStrategy,
    "buy_and_hold": BuyAndHoldStrategy,
    "mean_reversion": MeanReversionStrategy,
}


def get_strategy_class(strategy_id: str) -> type[Strategy]:
    """返回 strategy_id 对应的类。未注册抛 ``KeyError``。"""
    if strategy_id not in _REGISTRY:
        raise KeyError(
            f"unknown strategy_id {strategy_id!r}; available: {sorted(_REGISTRY.keys())}"
        )
    return _REGISTRY[strategy_id]


def list_strategies() -> list[str]:
    """已注册的所有 strategy_id。"""
    return sorted(_REGISTRY.keys())
