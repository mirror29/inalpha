"""执行层风控规则插件包。

[ADR-0006](../../../../../docs/miro/decisions/0006-risk-rules.md) 定义。

每个 `RiskRule` 在 `Strategy.submit_order()` → `RiskEngine.execute` 链路上
作为前置闸门。命中即拒单（写入 `OrderRejected` event）。

子模块：

- `base` —— `RiskRule` 抽象 + `RiskVerdict` + `TradeRepository` Protocol + 配置工具
- `cooldown` —— `CooldownRule`：单 symbol 冷却期

后续 Slice 加 `low_profit` / `max_drawdown` / `stoploss_guard` / `market_hours`。
"""
from __future__ import annotations

from .base import (
    ClosedTradeRecord,
    LockScope,
    RiskRule,
    RiskRuleConfigError,
    RiskVerdict,
    TradeRepository,
)
from .cooldown import CooldownRule

__all__ = [
    "ClosedTradeRecord",
    "CooldownRule",
    "LockScope",
    "RiskRule",
    "RiskRuleConfigError",
    "RiskVerdict",
    "TradeRepository",
]
