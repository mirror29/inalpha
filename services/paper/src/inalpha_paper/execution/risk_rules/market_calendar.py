"""``MarketCalendar`` 实现 —— ``RoutingCalendar`` 按 ``(venue, symbol)`` 派发到
``exchange_calendars`` 真实交易日历。

D-9.1a 收尾（多市场 ``MarketHoursRule`` 真生效）：原手写 ``USEquityCalendar`` +
``_CryptoOnlyCalendar`` 存在两个问题——(1) 美股 venue 集合写死 ``nasdaq``/``nyse``，
但系统实际 venue 是 ``yfinance``/``alpaca``，导致美股订单走 fail-open 永不拦；
(2) 无法覆盖 A股 / 港股 / 日英德 / 韩澳印 / 全球指数，且手维护假日表会 stale
（违背金融时效性硬约束）。

本实现改用 ``exchange_calendars``（量化标准库，自带各交易所节假日 / 午休 / 半日市 /
DST，抗 stale）作为全 D-9 市场的交易时段源：

- **crypto**（binance / coinbase / …）→ 24/7 永真（不查日历）
- **fred** → 宏观无交易时段 → 放行（不查日历，不告警）
- **其余** → :func:`exchange_resolver.resolve_calendar_code` 把 ``(venue, symbol)``
  解析成交易所 code（``XNYS`` / ``XSHG`` / ``XHKG`` …）→ 查 ``exchange_calendars``
- **无法解析**（未识别 venue / 未列入指数）→ log warning + fail-open（默认放行，
  不误拦合法订单）

时区：调用方传 UTC ``datetime``（D-9 默认 UTC）；``ExchangeCalendarsAdapter``
内部统一转成 tz-aware UTC ``pandas.Timestamp``。

盘前 / 盘后（``include_pre`` / ``include_after``）：MVP 暂忽略——``exchange_calendars``
按常规连续交易时段判断（含午休），盘前盘后留后续。
"""
from __future__ import annotations

import logging
from datetime import datetime
from functools import lru_cache

import exchange_calendars as xcals  # type: ignore[import-untyped]
import pandas as pd  # type: ignore[import-untyped]

from .exchange_resolver import _CRYPTO_VENUES, resolve_calendar_code

logger = logging.getLogger(__name__)


@lru_cache(maxsize=32)
def _get_calendar(code: str) -> xcals.ExchangeCalendar:
    """取 ``exchange_calendars`` 日历实例。

    缓存——首次构建会生成多年 schedule（较慢），同 code 复用同一实例。
    """
    return xcals.get_calendar(code)


def _to_utc_ts(now: datetime) -> pd.Timestamp:
    """``datetime`` → tz-aware UTC ``pandas.Timestamp``，floor 到分钟。

    naive 输入按 UTC 处理（D-9 默认 UTC）。
    """
    ts = pd.Timestamp(now)
    ts = ts.tz_localize("UTC") if ts.tz is None else ts.tz_convert("UTC")
    return ts.floor("min")


class ExchangeCalendarsAdapter:
    """包装 ``exchange_calendars``，按交易所 code 判断交易时段。

    内部 helper（按 code 而非 venue+symbol，不直接实现 ``MarketCalendar`` protocol）。
    """

    def is_open(self, code: str, now: datetime) -> bool:
        """``code`` 交易所在 ``now`` 是否处于连续交易时段（午休 / 盘后 / 假日 → False）。"""
        return bool(_get_calendar(code).is_open_on_minute(_to_utc_ts(now)))

    def next_open(self, code: str, now: datetime) -> datetime:
        """``now`` 之后下个 session 开盘时刻（tz-aware UTC ``datetime``）。"""
        nxt = _get_calendar(code).next_open(_to_utc_ts(now))
        out: datetime = nxt.to_pydatetime()
        return out


class RoutingCalendar:
    """按 ``(venue, symbol)`` 派发到 ``exchange_calendars``。

    实现 ``MarketCalendar`` protocol，供 :class:`MarketHoursRule` 注入。

    Args:
        default_open_on_unknown: 无法解析交易所时的行为——

            - ``True``（默认）：放行 + log warning，避免误拦未识别市场的合法订单
            - ``False``：按 closed 处理，更严格但易误拦未列入解析表的标的
    """

    def __init__(self, *, default_open_on_unknown: bool = True) -> None:
        self._adapter = ExchangeCalendarsAdapter()
        self._default_open = default_open_on_unknown

    def _is_always_open(self, venue: str) -> bool:
        """crypto（24/7）与 fred（无交易时段）→ 直接放行，不查日历、不告警。"""
        v = venue.strip().lower()
        return v in _CRYPTO_VENUES or v == "fred"

    def is_trading_hours(
        self,
        venue: str,
        symbol: str,
        now: datetime,
        *,
        include_pre: bool = False,
        include_after: bool = False,
    ) -> bool:
        if self._is_always_open(venue):
            return True
        code = resolve_calendar_code(venue, symbol)
        if code is None:
            logger.warning(
                "RoutingCalendar: 无法解析 venue=%r symbol=%r → fail-%s",
                venue,
                symbol,
                "open" if self._default_open else "closed",
            )
            return self._default_open
        # include_pre / include_after：MVP 忽略，按常规连续时段判断
        return self._adapter.is_open(code, now)

    def next_session_open(self, venue: str, symbol: str, now: datetime) -> datetime:
        if self._is_always_open(venue):
            return now
        code = resolve_calendar_code(venue, symbol)
        if code is None:
            return now
        return self._adapter.next_open(code, now)


__all__ = ["ExchangeCalendarsAdapter", "RoutingCalendar"]
