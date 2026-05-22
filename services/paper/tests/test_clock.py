"""``TestClock`` / ``LiveClock`` 的单测。"""
from __future__ import annotations

import pytest

from inalpha_paper.kernel.clock import LiveClock, TestClock, TimeEvent

# ─── TestClock 基础 ───


def test_test_clock_now() -> None:
    c = TestClock(initial_ns=1_000_000_000)
    assert c.now_ns() == 1_000_000_000
    assert c.now().year >= 1970  # aware datetime


def test_now_high_resolution_no_float_rounding() -> None:
    """ts_ns 走整数路径，不丢精度（D-8b' review 高风险 #5）。

    2026 年 ts_ns ≈ 1.7e18 超 float64 mantissa，旧版 fromtimestamp(ns / 1e9)
    会丢 ~100ns。修复后保留到 microsecond 精度。
    """
    # 2026-05-22 12:34:56.123456 → 1747917296123456000 ns
    ns_2026 = 1_747_917_296_123_456_000
    c = TestClock(initial_ns=ns_2026)
    dt = c.now()
    assert dt.year == 2025  # epoch + 1747917296s 落在 2025-05 (UTC)
    assert dt.microsecond == 123_456  # 微秒精度必须保留


def test_now_handles_microsecond_round_up_carry() -> None:
    """999_500ns 部分四舍五入到 1us，但 1_000_000us 必须进位到下一秒。"""
    # 1 秒 + 999_999_500ns = 应进位到 2 秒整
    c = TestClock(initial_ns=1_999_999_500)
    dt = c.now()
    assert dt.second == 2 % 60
    assert dt.microsecond == 0


def test_set_time_forward_only() -> None:
    c = TestClock(initial_ns=100)
    c.set_time(200)
    assert c.now_ns() == 200

    with pytest.raises(ValueError, match="cannot set_time backwards"):
        c.set_time(150)


def test_advance_time_no_timers() -> None:
    c = TestClock(initial_ns=0)
    triggered = c.advance_time(1_000_000)
    assert triggered == []
    assert c.now_ns() == 1_000_000


def test_advance_time_backwards_rejected() -> None:
    c = TestClock(initial_ns=1_000)
    with pytest.raises(ValueError, match="cannot advance_time backwards"):
        c.advance_time(500)


# ─── TestClock 定时器 ───


def test_timer_fires_once_within_interval() -> None:
    c = TestClock(initial_ns=0)
    captured: list[TimeEvent] = []

    c.set_timer("t1", interval_ns=100, callback=captured.append)
    triggered = c.advance_time(150)

    assert len(triggered) == 1
    assert triggered[0].name == "t1"
    assert triggered[0].ts_event == 100
    assert captured == triggered


def test_timer_fires_multiple_times() -> None:
    c = TestClock(initial_ns=0)
    captured: list[TimeEvent] = []

    c.set_timer("hb", interval_ns=10, callback=captured.append)
    triggered = c.advance_time(35)

    assert [e.ts_event for e in triggered] == [10, 20, 30]
    assert len(captured) == 3


def test_multiple_timers_ordered_by_ts() -> None:
    c = TestClock(initial_ns=0)
    captured: list[TimeEvent] = []

    c.set_timer("a", interval_ns=15, callback=captured.append)
    c.set_timer("b", interval_ns=10, callback=captured.append)
    c.advance_time(50)

    # 严格按 ts_event 升序
    names_by_ts = [e.name for e in captured]
    times = [e.ts_event for e in captured]
    assert times == sorted(times)
    assert "a" in names_by_ts and "b" in names_by_ts


def test_cancel_timer() -> None:
    c = TestClock(initial_ns=0)
    captured: list[TimeEvent] = []
    c.set_timer("t1", interval_ns=10, callback=captured.append)
    c.cancel_timer("t1")
    c.advance_time(100)
    assert captured == []


def test_cancel_nonexistent_timer_is_noop() -> None:
    c = TestClock(initial_ns=0)
    c.cancel_timer("doesnotexist")  # 不抛错


def test_timer_invalid_interval() -> None:
    c = TestClock(initial_ns=0)
    with pytest.raises(ValueError, match="interval_ns must be positive"):
        c.set_timer("bad", interval_ns=0, callback=lambda e: None)


# ─── LiveClock ───


def test_live_clock_now_is_recent() -> None:
    import time

    c = LiveClock()
    before = time.time_ns()
    got = c.now_ns()
    after = time.time_ns()
    assert before <= got <= after


def test_live_clock_set_timer_not_implemented() -> None:
    c = LiveClock()
    with pytest.raises(NotImplementedError):
        c.set_timer("t1", 1_000_000, lambda e: None)
