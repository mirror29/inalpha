"""`MarketHoursRule` —— 非交易时段拦截下单。Inalpha 新增（freqtrade 无对应）。

[ADR-0006 §D2](../../../../../docs/miro/decisions/0006-risk-rules.md) 表中第 5 件
（Inalpha 多市场场景必须 —— freqtrade 是纯 crypto 24×7 不需要）。

**触发条件**：`MarketCalendar.is_trading_hours(market, now) == False` → 锁市场。
**用途**：A股盘后 / 美股盘前盘后 / 港股午休等场景拦截下单。

`market` 参数取自 `InstrumentId.venue`（如 "binance" / "nasdaq" / "shanghai"），
具体 venue→交易时段的映射由 MarketCalendar 实现决定（接 `services/data` 真实日历）。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from .base import MarketCalendar, RiskRule, RiskVerdict, Side, TradeRepository


class MarketHoursRule(RiskRule):
    has_market_check = True

    def __init__(
        self,
        config: dict[str, Any],
        trade_repo: TradeRepository,
        market_calendar: MarketCalendar,
    ) -> None:
        super().__init__(config, trade_repo)
        self._calendar = market_calendar
        self._allow_pre_market = bool(config.get("allow_pre_market", False))
        self._allow_after_hours = bool(config.get("allow_after_hours", False))

    def short_desc(self) -> str:
        flags = []
        if self._allow_pre_market:
            flags.append("含盘前")
        if self._allow_after_hours:
            flags.append("含盘后")
        suffix = f"（{'/'.join(flags)}）" if flags else ""
        return f"{self.name} - 非交易时段拦截{suffix}"

    def check_market(
        self, market: str, now: datetime, side: Side, starting_balance: float
    ) -> RiskVerdict | None:
        if self._calendar.is_trading_hours(
            market,
            now,
            include_pre=self._allow_pre_market,
            include_after=self._allow_after_hours,
        ):
            return None

        next_open = self._calendar.next_session_open(market, now)
        return RiskVerdict(
            until=next_open,
            reason=f"市场 {market} 非交易时段（下次开盘 {next_open.isoformat()}）",
            rule_name=self.name,
            lock_side="*",
            lock_scope="market",
            lock_market=market,
        )
