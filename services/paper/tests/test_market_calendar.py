"""``RoutingCalendar`` / ``CryptoCalendar`` / ``USEquityCalendar`` 单测。

覆盖：

- CryptoCalendar 永远 open
- USEquityCalendar 工作日 9:30-16:00 ET 开 / 周末关 / DST 正确处理
- USEquityCalendar.next_session_open 三个边界（盘前 / 盘中 / 盘后 + 跨周末）
- RoutingCalendar venue 派发 / 大小写不敏感 / 未知 venue fail-open
- 未知 venue + default_open_on_unknown=False → 严格按 closed 处理
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from inalpha_paper.execution.risk_rules.market_calendar import (
    CryptoCalendar,
    RoutingCalendar,
    USEquityCalendar,
)

ET = ZoneInfo("America/New_York")


def _et(year: int, month: int, day: int, hour: int, minute: int) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=ET)


def _utc(year: int, month: int, day: int, hour: int, minute: int) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=UTC)


# ───────────── CryptoCalendar ─────────────


class TestCryptoCalendar:
    def test_always_open(self) -> None:
        cal = CryptoCalendar()
        # 周末凌晨 / 节假日 / 任意时间都开
        assert cal.is_trading_hours("binance", _utc(2026, 1, 1, 0, 0))  # 元旦 0 点
        assert cal.is_trading_hours("binance", _utc(2026, 5, 30, 3, 0))  # 周六 3 点
        assert cal.is_trading_hours("binance", _utc(2026, 12, 25, 12, 0))  # 圣诞节

    def test_next_session_open_is_now(self) -> None:
        cal = CryptoCalendar()
        now = _utc(2026, 5, 30, 3, 0)
        assert cal.next_session_open("binance", now) == now


# ───────────── USEquityCalendar ─────────────


class TestUSEquityCalendarIsOpen:
    def test_weekday_during_session(self) -> None:
        """周一 ET 10:00 (盘中) → open。2026-05-26 是周二。"""
        cal = USEquityCalendar()
        # 2026-05-26 (Tue) 10:00 ET
        assert cal.is_trading_hours("nasdaq", _et(2026, 5, 26, 10, 0))

    def test_at_open_boundary(self) -> None:
        """9:30 ET 整 → open（含等号）。"""
        cal = USEquityCalendar()
        assert cal.is_trading_hours("nasdaq", _et(2026, 5, 26, 9, 30))

    def test_just_before_open(self) -> None:
        cal = USEquityCalendar()
        assert not cal.is_trading_hours("nasdaq", _et(2026, 5, 26, 9, 29))

    def test_at_close_boundary(self) -> None:
        """16:00 ET 整 → closed（不含 16:00）。"""
        cal = USEquityCalendar()
        assert not cal.is_trading_hours("nasdaq", _et(2026, 5, 26, 16, 0))

    def test_just_before_close(self) -> None:
        cal = USEquityCalendar()
        assert cal.is_trading_hours("nasdaq", _et(2026, 5, 26, 15, 59))

    def test_saturday(self) -> None:
        """周六 ET 10:00 → closed。2026-05-30 是周六。"""
        cal = USEquityCalendar()
        assert not cal.is_trading_hours("nasdaq", _et(2026, 5, 30, 10, 0))

    def test_sunday(self) -> None:
        """周日 ET 10:00 → closed。2026-05-31 是周日。"""
        cal = USEquityCalendar()
        assert not cal.is_trading_hours("nasdaq", _et(2026, 5, 31, 10, 0))

    def test_utc_input_dst_active(self) -> None:
        """UTC 输入 DST 活跃期：2026-05-26 13:30 UTC = 9:30 EDT (UTC-4) → open。"""
        cal = USEquityCalendar()
        assert cal.is_trading_hours("nasdaq", _utc(2026, 5, 26, 13, 30))

    def test_utc_input_dst_inactive(self) -> None:
        """UTC 输入 DST 未生效：2026-01-13 14:30 UTC = 9:30 EST (UTC-5) → open。

        2026-01-13 是周二（standard time period）。
        """
        cal = USEquityCalendar()
        assert cal.is_trading_hours("nasdaq", _utc(2026, 1, 13, 14, 30))

    def test_utc_input_dst_boundary_off_by_one_hour(self) -> None:
        """DST 期间 14:30 UTC = 10:30 EDT 已经盘中，但不是 open 时刻。"""
        cal = USEquityCalendar()
        # 14:30 UTC during DST = 10:30 ET → open (1h into session)
        assert cal.is_trading_hours("nasdaq", _utc(2026, 5, 26, 14, 30))
        # 13:29 UTC during DST = 9:29 ET → closed (pre-open)
        assert not cal.is_trading_hours("nasdaq", _utc(2026, 5, 26, 13, 29))


class TestUSEquityCalendarNextOpen:
    def test_before_open_same_day(self) -> None:
        """Tue 8:00 ET → next open = 同日 9:30 ET。"""
        cal = USEquityCalendar()
        now = _et(2026, 5, 26, 8, 0)
        nxt = cal.next_session_open("nasdaq", now)
        assert nxt == _et(2026, 5, 26, 9, 30)

    def test_after_open_to_next_weekday(self) -> None:
        """Tue 17:00 ET → next open = Wed 9:30 ET。"""
        cal = USEquityCalendar()
        now = _et(2026, 5, 26, 17, 0)
        nxt = cal.next_session_open("nasdaq", now)
        assert nxt == _et(2026, 5, 27, 9, 30)

    def test_friday_evening_to_monday(self) -> None:
        """Fri 17:00 ET → 跨周末 → next Mon 9:30 ET。2026-05-29 Fri，6-1 Mon。"""
        cal = USEquityCalendar()
        now = _et(2026, 5, 29, 17, 0)
        nxt = cal.next_session_open("nasdaq", now)
        assert nxt == _et(2026, 6, 1, 9, 30)

    def test_saturday_to_monday(self) -> None:
        cal = USEquityCalendar()
        now = _et(2026, 5, 30, 12, 0)
        nxt = cal.next_session_open("nasdaq", now)
        assert nxt == _et(2026, 6, 1, 9, 30)

    def test_utc_input_returns_in_utc(self) -> None:
        """传 UTC 输入，返也是 UTC（保持 tzinfo 一致）。"""
        cal = USEquityCalendar()
        now = _utc(2026, 5, 26, 12, 0)
        nxt = cal.next_session_open("nasdaq", now)
        # 2026-05-26 13:30 UTC = 9:30 ET DST
        assert nxt == _utc(2026, 5, 26, 13, 30)
        assert nxt.tzinfo == UTC


# ───────────── RoutingCalendar ─────────────


class TestRoutingCalendar:
    def test_binance_routes_to_crypto(self) -> None:
        cal = RoutingCalendar()
        # Sat 凌晨 binance → 仍 open（crypto 永开）
        assert cal.is_trading_hours("binance", _utc(2026, 5, 30, 3, 0))

    def test_nasdaq_routes_to_us_equity(self) -> None:
        cal = RoutingCalendar()
        # Sat nasdaq → closed（美股周末关）
        assert not cal.is_trading_hours("nasdaq", _utc(2026, 5, 30, 13, 30))
        # Tue 13:30 UTC = 9:30 EDT → open
        assert cal.is_trading_hours("nasdaq", _utc(2026, 5, 26, 13, 30))

    def test_case_insensitive(self) -> None:
        cal = RoutingCalendar()
        assert cal.is_trading_hours("BINANCE", _utc(2026, 5, 30, 3, 0))
        assert cal.is_trading_hours("NASDAQ", _utc(2026, 5, 26, 13, 30))

    def test_unknown_venue_default_open(self) -> None:
        """未注册 venue + default_open=True（默认）→ True。"""
        cal = RoutingCalendar()
        assert cal.is_trading_hours("xyz_unknown", _utc(2026, 5, 26, 0, 0))

    def test_unknown_venue_strict_mode(self) -> None:
        """default_open=False → 未注册 venue 按 closed 处理。"""
        cal = RoutingCalendar(default_open_on_unknown=False)
        assert not cal.is_trading_hours("xyz_unknown", _utc(2026, 5, 26, 0, 0))

    def test_unknown_venue_next_session_open_returns_now(self) -> None:
        cal = RoutingCalendar()
        now = _utc(2026, 5, 26, 0, 0)
        assert cal.next_session_open("xyz_unknown", now) == now

    def test_next_session_open_nasdaq(self) -> None:
        cal = RoutingCalendar()
        now = _et(2026, 5, 26, 17, 0)
        nxt = cal.next_session_open("nasdaq", now)
        assert nxt == _et(2026, 5, 27, 9, 30)

    def test_unknown_warning_logged(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        cal = RoutingCalendar()
        with caplog.at_level("WARNING"):
            cal.is_trading_hours("strange_venue", _utc(2026, 5, 26, 0, 0))
        assert any("strange_venue" in rec.message for rec in caplog.records)


# ───────────── 健全性：日期标定 ─────────────


def test_anchor_dates_are_correct() -> None:
    """sanity check：测试里假设的日期对应的星期几。"""
    assert _et(2026, 5, 26, 12, 0).weekday() == 1  # Tue
    assert _et(2026, 5, 29, 12, 0).weekday() == 4  # Fri
    assert _et(2026, 5, 30, 12, 0).weekday() == 5  # Sat
    assert _et(2026, 5, 31, 12, 0).weekday() == 6  # Sun
    assert _et(2026, 6, 1, 12, 0).weekday() == 0   # Mon
    # 2026-05-26 处于 DST 期间（EDT, UTC-4）
    assert _et(2026, 5, 26, 12, 0).utcoffset() == timedelta(hours=-4)
    # 2026-01-13 处于 standard time（EST, UTC-5）
    assert _et(2026, 1, 13, 12, 0).utcoffset() == timedelta(hours=-5)
