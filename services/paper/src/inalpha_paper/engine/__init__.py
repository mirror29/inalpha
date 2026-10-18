"""引擎：Portfolio + BacktestEngine + Report。

``BacktestEngine`` 把 data + kernel + execution 串起来跑完整回测闭环。
模拟盘不走独立的 LiveEngine：``LiveRunnerManager`` 用 ``engine/live_session.py`` 把
已收盘 bar 喂给同一个内核（``TestClock`` 驱动），live runner 的调度、审批与落库在
``live_runner.py``。
"""
from .backtest import BacktestEngine
from .portfolio import Portfolio
from .report import BacktestReport

__all__ = ["BacktestEngine", "BacktestReport", "Portfolio"]
