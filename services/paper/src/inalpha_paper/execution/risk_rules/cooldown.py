"""`CooldownRule` —— 单 symbol 冷却期。

[ADR-0006 §D2](../../../../../docs/miro/decisions/0006-risk-rules.md#d2--5-件套v1-范围)
表中第 1 件。直翻 freqtrade `CooldownPeriod`（借鉴设计 + 中文化）。

**触发条件**：lookback 窗口内该 symbol 有已平仓 trade → 锁该 symbol。
**用途**：防同一 symbol 反复出入（LLM 决策抖动 / 策略震荡）。
"""
from __future__ import annotations

from datetime import datetime, timedelta

from ...kernel.identifiers import InstrumentId
from .base import RiskRule, RiskVerdict, Side


class CooldownRule(RiskRule):
    has_symbol_check = True

    def short_desc(self) -> str:
        if self._unlock_at is not None:
            hh, mm = self._unlock_at
            return f"CooldownRule - 单 symbol 冷却到 {hh:02d}:{mm:02d}"
        return f"CooldownRule - 单 symbol 冷却 {self._stop_duration_min} 分钟"

    def check_symbol(
        self,
        instrument_id: InstrumentId,
        now: datetime,
        side: Side,
        starting_balance: float,
    ) -> RiskVerdict | None:
        lookback_floor = now - timedelta(minutes=self._lookback_min)
        trades = self._trade_repo.get_closed_trades(
            instrument_id=instrument_id,
            close_after=lookback_floor,
        )
        if not trades:
            return None

        until = self.calculate_lock_end(trades, now)
        return RiskVerdict(
            until=until,
            reason=f"冷却期：{self._stop_duration_min} 分钟内已有 {len(trades)} 笔平仓",
            rule_name=self.name,
            lock_side="*",
            lock_scope="symbol",
        )
