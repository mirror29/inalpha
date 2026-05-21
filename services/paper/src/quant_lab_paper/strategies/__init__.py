"""用户策略实现（例子）+ 策略注册表。

D-5 起步只有 ``SMACrossStrategy``。后续 D-7+ 会有更多策略示例，最终在 Phase F+ 接受
外部贡献时再考虑 [ADR-0017 layer 1 沙盒](../../../../docs/decisions/0017-isolation-and-sandboxing.md)。

策略注册表给 D-6 起的 ``POST /backtest`` 用 —— 通过 ``strategy_id`` 字符串
路由到具体类。
"""
from ..strategy.base import Strategy
from .sma_cross import SMACrossStrategy

__all__ = ["SMACrossStrategy", "get_strategy_class", "list_strategies"]


# 简单字典 registry。D-7+ 多策略时可扩成 entry-points 自动发现。
_REGISTRY: dict[str, type[Strategy]] = {
    "sma_cross": SMACrossStrategy,
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
