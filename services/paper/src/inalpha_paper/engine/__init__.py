"""引擎：Portfolio + BacktestEngine + Report。

D-5 范围：``BacktestEngine`` 把 D-3 data + D-4 kernel + D-5 execution 串起来跑完整闭环。
D-6 起：``LiveEngine``（asyncio 事件循环 + LiveGateway）。
"""
from .backtest import BacktestEngine
from .portfolio import Portfolio
from .report import BacktestReport

__all__ = ["BacktestEngine", "BacktestReport", "Portfolio"]
