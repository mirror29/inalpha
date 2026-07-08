"""HINT 发生器 —— E1 用 4 条硬编码 hint 轮流。

E2 将替换为 LLM 生成的高阶 hint（基于上一代候选的表现分析）。
"""
from __future__ import annotations

from typing import Final

# ── E1 四条硬编码变异方向 ──────────────────────────────────────────
_HINTS: Final[list[str]] = [
    "改进入场时机：增加成交量确认过滤器，避免低流动性假突破",
    "降低回撤：添加跟踪止损（trailing stop）逻辑，保护盈利",
    "参数调优：调整快线和慢线周期，寻找更优参数组合",
    "增加风控：添加最大持仓天数限制和波动率过滤器",
]


class HintGenerator:
    """按顺序循环返回 hint。"""

    def __init__(self) -> None:
        self._index = 0

    def next(self) -> str:
        h = _HINTS[self._index]
        self._index = (self._index + 1) % len(_HINTS)
        return h

    def reset(self) -> None:
        self._index = 0