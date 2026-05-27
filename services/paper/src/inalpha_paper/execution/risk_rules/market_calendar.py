"""MarketCalendar 实现集 —— ``RoutingCalendar`` 按 venue 派发到对应子日历。

D-9.1a 第二阶段（issue #8 task #3）：让 ``MarketHoursRule`` 在非 crypto 市场
也真生效。原 ``main.py._CryptoOnlyCalendar`` 任何 venue 都返 True，导致
``MarketHoursRule`` 在美股 venue 永远 pass。

MVP 范围：

- **crypto**（binance / coinbase / okx / bybit / kraken / kucoin / 等）→ 24/7 永远开市
- **美股**（nasdaq / nyse）→ 9:30-16:00 America/New_York，工作日；**不含假日**
- **未知 venue** → log warning + fail-open（默认 ``default_open_on_unknown=True``，
  避免误拦未识别市场的合法订单）

不在 MVP：

- 美股假日（感恩节、圣诞节、独立日 等）—— 需要假日表，留 D-10+
  （建议 D-10 接 ``pandas_market_calendars`` 或类似 lib，本模块抽象足够 swap）
- 盘前 / 盘后（``include_pre`` / ``include_after`` 暂忽略——MVP 全按常规时段判断）
- A 股 / 港股 / 日股 / 欧股 venue 接入

时区：

- ``USEquityCalendar`` 内部用 ``zoneinfo.ZoneInfo("America/New_York")`` 处理 DST，
  调用方传 UTC ``datetime`` 即可（D-9 默认 UTC）
"""
from __future__ import annotations

import logging
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from .base import MarketCalendar

logger = logging.getLogger(__name__)


# 已知 venue 分类。venue 字符串小写比较。
_CRYPTO_VENUES: frozenset[str] = frozenset({
    "binance",
    "coinbase",
    "okx",
    "bybit",
    "kraken",
    "kucoin",
    "bitfinex",
    "huobi",
    "gate",
    "mexc",
})

_US_EQUITY_VENUES: frozenset[str] = frozenset({
    "nasdaq",
    "nyse",
    "us_equity",
    "arca",
    "bats",
})

_US_EQUITY_TZ = ZoneInfo("America/New_York")
_US_EQUITY_OPEN = time(9, 30)
_US_EQUITY_CLOSE = time(16, 0)


class CryptoCalendar:
    """crypto venue —— 24/7 永远开市。

    ``next_session_open`` 直接返 ``now``（任何时刻都已经在交易时段）。
    """

    def is_trading_hours(
        self,
        market: str,
        now: datetime,
        *,
        include_pre: bool = False,
        include_after: bool = False,
    ) -> bool:
        return True

    def next_session_open(self, market: str, now: datetime) -> datetime:
        return now


class USEquityCalendar:
    """美股 NYSE / NASDAQ —— 9:30-16:00 America/New_York，仅工作日，**不含假日**。

    DST 由 ``zoneinfo`` 自动处理。``include_pre`` / ``include_after`` MVP 忽略。
    """

    def is_trading_hours(
        self,
        market: str,
        now: datetime,
        *,
        include_pre: bool = False,
        include_after: bool = False,
    ) -> bool:
        et_now = now.astimezone(_US_EQUITY_TZ)
        if et_now.weekday() >= 5:  # Sat=5, Sun=6
            return False
        et_time = et_now.time()
        return _US_EQUITY_OPEN <= et_time < _US_EQUITY_CLOSE

    def next_session_open(self, market: str, now: datetime) -> datetime:
        """``now`` 之后下个 9:30 ET。处理：当日已过 9:30 → 下一日；跳过周末。"""
        et_now = now.astimezone(_US_EQUITY_TZ)
        candidate = et_now.replace(
            hour=_US_EQUITY_OPEN.hour,
            minute=_US_EQUITY_OPEN.minute,
            second=0,
            microsecond=0,
        )
        if candidate <= et_now:
            candidate = candidate + timedelta(days=1)
        while candidate.weekday() >= 5:
            candidate = candidate + timedelta(days=1)
        if now.tzinfo is None:
            return candidate.replace(tzinfo=None)
        return candidate.astimezone(now.tzinfo)


class RoutingCalendar:
    """按 venue 字符串派发到对应子日历。

    Args:
        crypto_calendar: 处理 crypto venue（默认 :class:`CryptoCalendar`）
        us_equity_calendar: 处理美股 venue（默认 :class:`USEquityCalendar`）
        default_open_on_unknown: 未识别 venue 的行为：

            - True（默认）：返 True + log warning，避免误拦合法订单
            - False：按 closed 处理，更严格但易误拦未列入注册表的 venue

    扩展：增加新市场（如 A 股）只需另写 ``ChinaEquityCalendar`` + 注入新 venues。
    """

    def __init__(
        self,
        *,
        crypto_calendar: MarketCalendar | None = None,
        us_equity_calendar: MarketCalendar | None = None,
        default_open_on_unknown: bool = True,
    ) -> None:
        self._crypto = crypto_calendar or CryptoCalendar()
        self._us = us_equity_calendar or USEquityCalendar()
        self._default_open = default_open_on_unknown

    def _route(self, market: str) -> MarketCalendar | None:
        m = market.lower()
        if m in _CRYPTO_VENUES:
            return self._crypto
        if m in _US_EQUITY_VENUES:
            return self._us
        return None

    def is_trading_hours(
        self,
        market: str,
        now: datetime,
        *,
        include_pre: bool = False,
        include_after: bool = False,
    ) -> bool:
        cal = self._route(market)
        if cal is None:
            logger.warning(
                "RoutingCalendar: unknown venue=%r → fail-%s (default_open_on_unknown=%s)",
                market,
                "open" if self._default_open else "closed",
                self._default_open,
            )
            return self._default_open
        return cal.is_trading_hours(
            market, now, include_pre=include_pre, include_after=include_after,
        )

    def next_session_open(self, market: str, now: datetime) -> datetime:
        cal = self._route(market)
        if cal is None:
            return now
        return cal.next_session_open(market, now)


__all__ = ["CryptoCalendar", "RoutingCalendar", "USEquityCalendar"]
