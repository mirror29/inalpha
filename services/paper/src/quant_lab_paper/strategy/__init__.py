"""Strategy / Actor —— 用户写策略的两个基类。

二层分层（参考 [refs/nautilus.md §3](../../../../docs/refs/nautilus.md)）：

- ``Actor`` —— 只订阅数据 / 处理事件，不下单。用于纯研究 / indicator 计算
- ``Strategy`` —— extends Actor，加 ``submit_order`` / ``cancel_order`` / ``close_position``
"""
from .actor import Actor
from .base import Strategy

__all__ = ["Actor", "Strategy"]
