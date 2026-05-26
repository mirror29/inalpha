"""`StoplossGuardRule` —— 短窗口内连续止损则锁。

[ADR-0006 §D2](../../../../../docs/miro/decisions/0006-risk-rules.md) 表中第 4 件。
直翻 freqtrade `StoplossGuard`（借鉴设计 + 中文化）。

**触发条件**：lookback 窗口内 stop-loss / trailing-stop / liquidation 退出且亏损 < `required_profit`
的 trade 数 >= `trade_limit` → 锁。

**双层**（global + symbol）：
- `only_per_symbol=True` 时只跑 symbol 级
- 否则同时支持 global（看全部 symbol）和 symbol（限定单 symbol）
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from ...kernel.identifiers import InstrumentId
from .base import RiskRule, RiskRuleConfigError, RiskVerdict, Side, TradeRepository

_STOP_LOSS_REASONS = ("stop_loss", "trailing_stop_loss", "liquidation")


class StoplossGuardRule(RiskRule):
    has_global_check = True
    has_symbol_check = True

    def __init__(self, config: dict[str, Any], trade_repo: TradeRepository) -> None:
        super().__init__(config, trade_repo)
        self._trade_limit = int(config.get("trade_limit", 10))
        self._only_per_symbol = bool(config.get("only_per_symbol", False))
        self._only_per_side = bool(config.get("only_per_side", False))
        self._required_profit = float(config.get("required_profit", 0.0))
        if self._trade_limit <= 0:
            raise RiskRuleConfigError(
                f"{self.name}: trade_limit must be positive, got {self._trade_limit}"
            )

    def short_desc(self) -> str:
        scope_desc = "单 symbol" if self._only_per_symbol else "全局 + 单 symbol"
        return (
            f"{self.name} - {self._trade_limit} 次止损（{scope_desc}，窗口 "
            f"{self._lookback_min} 分钟）即锁"
        )

    def check_global(
        self, now: datetime, side: Side, starting_balance: float
    ) -> RiskVerdict | None:
        if self._only_per_symbol:
            return None
        return self._count_check(now, instrument_id=None, side=side, scope="global")

    def check_symbol(
        self,
        instrument_id: InstrumentId,
        now: datetime,
        side: Side,
        starting_balance: float,
    ) -> RiskVerdict | None:
        return self._count_check(now, instrument_id=instrument_id, side=side, scope="symbol")

    def _count_check(
        self,
        now: datetime,
        *,
        instrument_id: InstrumentId | None,
        side: Side,
        scope: str,
    ) -> RiskVerdict | None:
        trades = self._trade_repo.get_closed_trades(
            instrument_id=instrument_id,
            close_after=now - timedelta(minutes=self._lookback_min),
            side=side if self._only_per_side else None,
            exit_reasons=list(_STOP_LOSS_REASONS),
            max_profit_pct=self._required_profit,
        )
        if len(trades) < self._trade_limit:
            return None

        until = self.calculate_lock_end(trades, now)
        return RiskVerdict(
            until=until,
            reason=(
                f"{len(trades)} 次止损在 {self._lookback_min} 分钟内"
                f"（阈值 {self._trade_limit}）"
            ),
            rule_name=self.name,
            lock_side=side if self._only_per_side else "*",
            lock_scope="symbol" if scope == "symbol" else "global",
        )
