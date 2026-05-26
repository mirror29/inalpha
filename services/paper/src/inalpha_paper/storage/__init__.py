"""paper service 持久层（D-8b 起）。

模块拆分：
- ``accounts`` —— 用户级虚拟账户（cash + initial_cash）
- ``orders`` —— 订单流水（FILLED / REJECTED 都落盘）
- ``positions`` —— 用户级持仓累计（每根 fill 后 reduce 更新）
- ``trade_plans`` —— Plan/Exec 计划（替代 orchestration 进程内 PlanStore）

设计约定：
- 所有函数都接 ``AsyncConnection`` 参数（不内部 acquire 连接），让调用方控制事务
- 一次 fill 涉及"写 orders + 更新 positions + 扣 cash"——必须在调用方的事务内串起来
"""
from . import (
    accounts,
    backtest_runs,
    closed_trades,
    orders,
    positions,
    risk_locks,
    trade_plans,
)

__all__ = [
    "accounts",
    "backtest_runs",
    "closed_trades",
    "orders",
    "positions",
    "risk_locks",
    "trade_plans",
]
