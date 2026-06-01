"""``RoutingCalendar`` + ``exchange_calendars`` 接入单测。

用**固定历史日期**断言各市场开 / 闭市（午休 / 周末 / 假日），不依赖"今天"。
锚定日期（均为 UTC）：

- 2024-01-01 周一 = 美股元旦休市
- 2024-01-02 周二 = 美股 / A股 / 港股正常交易日
- 2024-01-04 周四 = 日股正常交易日（1/1–1/3 日本休市）
- 2024-01-06 周六 = 周末
"""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from inalpha_paper.execution.risk_rules.market_calendar import RoutingCalendar


def _utc(year: int, month: int, day: int, hour: int = 0, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=UTC)


def test_anchor_weekdays() -> None:
    """sanity check：锚定日期的星期几。"""
    assert _utc(2024, 1, 1).weekday() == 0  # Mon（元旦）
    assert _utc(2024, 1, 2).weekday() == 1  # Tue
    assert _utc(2024, 1, 4).weekday() == 3  # Thu
    assert _utc(2024, 1, 6).weekday() == 5  # Sat


# ───────────── crypto（24/7） ─────────────


class TestCrypto:
    def test_always_open(self) -> None:
        cal = RoutingCalendar()
        assert cal.is_trading_hours("binance", "BTC/USDT", _utc(2024, 1, 6, 3, 0))  # 周六
        assert cal.is_trading_hours("binance", "BTC/USDT", _utc(2024, 1, 1, 0, 0))  # 元旦

    def test_case_insensitive(self) -> None:
        cal = RoutingCalendar()
        assert cal.is_trading_hours("BINANCE", "BTC/USDT", _utc(2024, 1, 6, 3, 0))

    def test_next_open_is_now(self) -> None:
        cal = RoutingCalendar()
        now = _utc(2024, 1, 6, 3, 0)
        assert cal.next_session_open("binance", "BTC/USDT", now) == now


# ───────────── 美股 XNYS（yfinance / alpaca） ─────────────


class TestUSEquity:
    def test_open_during_session(self) -> None:
        # 2024-01-02 Tue 10:00 ET (EST) = 15:00 UTC
        assert RoutingCalendar().is_trading_hours("yfinance", "AAPL", _utc(2024, 1, 2, 15, 0))

    def test_closed_weekend(self) -> None:
        assert not RoutingCalendar().is_trading_hours("yfinance", "AAPL", _utc(2024, 1, 6, 15, 0))

    def test_closed_new_year_holiday(self) -> None:
        # 2024-01-01 元旦休市
        assert not RoutingCalendar().is_trading_hours("yfinance", "AAPL", _utc(2024, 1, 1, 15, 0))

    def test_alpaca_routes_same(self) -> None:
        assert RoutingCalendar().is_trading_hours("alpaca", "TSLA", _utc(2024, 1, 2, 15, 0))

    def test_next_open_after_close(self) -> None:
        cal = RoutingCalendar()
        now = _utc(2024, 1, 2, 22, 0)  # 美东盘后
        nxt = cal.next_session_open("yfinance", "AAPL", now)
        assert nxt > now
        assert nxt.tzinfo is not None


# ───────────── A股 XSHG（akshare sh./sz.） ─────────────


class TestAShare:
    def test_open_morning(self) -> None:
        # 2024-01-02 Tue 10:00 CST = 02:00 UTC
        assert RoutingCalendar().is_trading_hours("akshare", "sh.600519", _utc(2024, 1, 2, 2, 0))

    def test_closed_lunch_break(self) -> None:
        # 12:00 CST = 04:00 UTC（午休）
        assert not RoutingCalendar().is_trading_hours("akshare", "sh.600519", _utc(2024, 1, 2, 4, 0))

    def test_shenzhen_routes_same(self) -> None:
        # sz. 复用 XSHG
        assert RoutingCalendar().is_trading_hours("akshare", "sz.000001", _utc(2024, 1, 2, 2, 0))

    def test_closed_weekend(self) -> None:
        assert not RoutingCalendar().is_trading_hours("akshare", "sh.600519", _utc(2024, 1, 6, 2, 0))


# ───────────── 港股 XHKG / 日股 XTKS ─────────────


class TestHongKongJapan:
    def test_hk_open_morning(self) -> None:
        # 2024-01-02 Tue 10:30 HKT = 02:30 UTC
        assert RoutingCalendar().is_trading_hours("akshare", "hk.00700", _utc(2024, 1, 2, 2, 30))

    def test_hk_closed_weekend(self) -> None:
        assert not RoutingCalendar().is_trading_hours("akshare", "hk.00700", _utc(2024, 1, 6, 2, 30))

    def test_jp_index_routes_tokyo(self) -> None:
        # ^N225 → XTKS。2024-01-04 Thu 10:00 JST = 01:00 UTC（1/1–1/3 日本休市）
        assert RoutingCalendar().is_trading_hours("yfinance", "^N225", _utc(2024, 1, 4, 1, 0))

    def test_jp_closed_holiday(self) -> None:
        # 2024-01-02 日本仍休市
        assert not RoutingCalendar().is_trading_hours("yfinance", "7203.T", _utc(2024, 1, 2, 1, 0))


# ───────────── fred / 未识别 venue ─────────────


class TestFredAndUnknown:
    def test_fred_always_open_no_warning(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        cal = RoutingCalendar()
        with caplog.at_level("WARNING"):
            assert cal.is_trading_hours("fred", "DFF", _utc(2024, 1, 6, 3, 0))
        assert not any("DFF" in rec.message for rec in caplog.records)

    def test_unknown_venue_fail_open(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        cal = RoutingCalendar()
        with caplog.at_level("WARNING"):
            assert cal.is_trading_hours("weird_venue", "XYZ", _utc(2024, 1, 6, 3, 0))
        assert any("weird_venue" in rec.message for rec in caplog.records)

    def test_unknown_venue_strict_mode_closed(self) -> None:
        cal = RoutingCalendar(default_open_on_unknown=False)
        assert not cal.is_trading_hours("weird_venue", "XYZ", _utc(2024, 1, 6, 3, 0))

    def test_unknown_venue_next_open_returns_now(self) -> None:
        cal = RoutingCalendar()
        now = _utc(2024, 1, 6, 3, 0)
        assert cal.next_session_open("weird_venue", "XYZ", now) == now
