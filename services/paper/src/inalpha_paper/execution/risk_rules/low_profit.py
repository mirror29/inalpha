"""`LowProfitRule` —— 单 symbol 累计盈利低于阈值。

[ADR-0006 §D2](../../../../../docs/miro/decisions/0006-risk-rules.md) 表中第 2 件。
直翻 freqtrade `LowProfitPairs`（借鉴设计 + 中文化）。

**触发条件**：lookback 窗口内该 symbol 已平仓 trade 累计盈亏 < required_profit 阈值 → 锁。
**用途**：锁持续亏损的 symbol。
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from ...kernel.identifiers import InstrumentId
from .base import RiskRule, RiskRuleConfigError, RiskVerdict, Side, TradeRepository


class LowProfitRule(RiskRule):
    has_symbol_check = True

    def __init__(self, config: dict[str, Any], trade_repo: TradeRepository) -> None:
        super().__init__(config, trade_repo)
        self._trade_limit = int(config.get("trade_limit", 1))
        self._required_profit = float(config.get("required_profit", 0.0))
        self._only_per_side = bool(config.get("only_per_side", False))
        if self._trade_limit <= 0:
            raise RiskRuleConfigError(
                f"{self.name}: trade_limit must be positive, got {self._trade_limit}"
            )

    def short_desc(self) -> str:
        return (
            f"{self.name} - 锁累计盈亏 < {self._required_profit:.2%} "
            f"的 symbol（至少 {self._trade_limit} 笔，窗口 {self._lookback_min} 分钟）"
        )

    def check_symbol(
        self,
        instrument_id: InstrumentId,
        now: datetime,
        side: Side,
        starting_balance: float,
    ) -> RiskVerdict | None:
        trades = self._trade_repo.get_closed_trades(
            instrument_id=instrument_id,
            close_after=now - timedelta(minutes=self._lookback_min),
            side=side if self._only_per_side else None,
        )
        if len(trades) < self._trade_limit:
            return None

        cumulative = sum(t.close_profit_pct for t in trades)
        if cumulative >= self._required_profit:
            return None

        until = self.calculate_lock_end(trades, now)
        return RiskVerdict(
            until=until,
            reason=(
                f"累计盈亏 {cumulative:.2%} < {self._required_profit:.2%}"
                f"（{len(trades)} 笔，窗口 {self._lookback_min} 分钟）"
            ),
            rule_name=self.name,
            lock_side=side if self._only_per_side else "*",
            lock_scope="symbol",
        )
