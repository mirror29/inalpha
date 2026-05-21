"""用户策略实现（例子）。

D-5 起步只有 ``SMACrossStrategy``。后续 D-7+ 会有更多策略示例，最终在 Phase F+ 接受
外部贡献时再考虑 [ADR-0017 layer 1 沙盒](../../../../docs/decisions/0017-isolation-and-sandboxing.md)。
"""
from .sma_cross import SMACrossStrategy

__all__ = ["SMACrossStrategy"]
