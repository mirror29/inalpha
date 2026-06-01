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

from ...kernel.identifiers import InstrumentId
from .base import MarketCalendar, RiskRule, RiskVerdict, Side, TradeRepository
from .exchange_resolver import resolve_calendar_code


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
        self,
        instrument_id: InstrumentId,
        now: datetime,
        side: Side,
        starting_balance: float,
    ) -> RiskVerdict | None:
        venue = instrument_id.venue
        symbol = instrument_id.symbol
        if self._calendar.is_trading_hours(
            venue,
            symbol,
            now,
            include_pre=self._allow_pre_market,
            include_after=self._allow_after_hours,
        ):
            return None

        next_open = self._calendar.next_session_open(venue, symbol, now)
        # 锁键用交易所日历 code（同交易所共享开闭市），无法解析时 fallback venue
        lock_key = resolve_calendar_code(venue, symbol) or venue
        return RiskVerdict(
            until=next_open,
            reason=f"市场 {lock_key} 非交易时段（下次开盘 {next_open.isoformat()}）",
            rule_name=self.name,
            lock_side="*",
            lock_scope="market",
            lock_market=lock_key,
        )
