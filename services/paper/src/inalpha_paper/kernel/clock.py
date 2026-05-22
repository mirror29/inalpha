"""时间源抽象。

设计依据：[refs/nautilus.md §4 · 回测 vs 实盘统一机制](../../../../docs/refs/nautilus.md)。

- ``TestClock``：回测用，时间由数据驱动（``set_time`` / ``advance_time``）
- ``LiveClock``：实盘 / 模拟盘用，时间由 ``time.time_ns()`` 驱动
- 所有组件经 ``Clock`` 取时间，**绝不**直接调 ``time.time()`` —— 这是
  "backtest = live"不变量的物理基础
"""
from __future__ import annotations

import time
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime


@dataclass(frozen=True, slots=True)
class TimeEvent:
    """定时器触发时投递给 callback 的事件。"""

    name: str
    ts_event: int  # 触发时刻（ns，UTC epoch）
    ts_init: int  # 注入系统的时刻（与 ts_event 相同，timer 是同源）


TimerCallback = Callable[[TimeEvent], None]


class Clock(ABC):
    """时间源抽象基类。"""

    @abstractmethod
    def now_ns(self) -> int:
        """当前时间（纳秒 since UTC epoch）。"""

    def now(self) -> datetime:
        """当前时间（aware datetime, UTC）。

        实现注：用整除把秒、纳秒拆开再合 microsecond，避免 float 损失精度
        （ts_ns ≈ 1.7e18 已超 float64 mantissa 精度，2026 年起 / 1e9 会丢 ~100ns）。
        """
        ns = self.now_ns()
        secs, ns_rem = divmod(ns, 1_000_000_000)
        # datetime 只到 microsecond，纳秒精度做四舍五入到 us
        micros = (ns_rem + 500) // 1_000
        # 进位（999_500ns → 1_000_000us 需要顺延到下一秒）
        if micros >= 1_000_000:
            secs += 1
            micros -= 1_000_000
        return datetime.fromtimestamp(secs, tz=UTC).replace(microsecond=micros)

    @abstractmethod
    def set_timer(self, name: str, interval_ns: int, callback: TimerCallback) -> None:
        """注册一个周期触发的定时器。同名 timer 会被覆盖。"""

    @abstractmethod
    def cancel_timer(self, name: str) -> None:
        """取消定时器。不存在的 name 静默跳过。"""


# ─── TestClock ───


class TestClock(Clock):
    """回测时钟：时间由调用方推进。

    ``advance_time(to_ns)`` 把时间推进到 ``to_ns``，过程中触发所有在 ``[当前, to_ns]``
    区间内到期的定时器。所有定时器**严格按 ts_event 升序**触发；同 ts 的按注册顺序。
    """

    # 让 pytest 不要把它当 test class 收集（因为名字以 Test 开头）
    __test__ = False

    def __init__(self, initial_ns: int = 0) -> None:
        self._now_ns: int = initial_ns
        # name -> (next_fire_ns, interval_ns, callback)
        self._timers: dict[str, tuple[int, int, TimerCallback]] = {}

    def now_ns(self) -> int:
        return self._now_ns

    def set_time(self, ns: int) -> None:
        """强制设置当前时间（不触发定时器；用 ``advance_time`` 推进会触发）。"""
        if ns < self._now_ns:
            raise ValueError(f"cannot set_time backwards: {ns} < {self._now_ns}")
        self._now_ns = ns

    def advance_time(self, to_ns: int) -> list[TimeEvent]:
        """推进时间到 ``to_ns``，返回所有触发的 ``TimeEvent``（按 ts_event 升序）。

        - 同名 timer 触发后自动按 ``interval_ns`` 重新排期
        - 一次 ``advance_time`` 内同 timer 可能多次触发（如 interval=1s 推进 5s 会触发 5 次）
        """
        if to_ns < self._now_ns:
            raise ValueError(f"cannot advance_time backwards: {to_ns} < {self._now_ns}")

        triggered: list[TimeEvent] = []
        while True:
            # 找下一个最早到期的 timer
            candidates = [
                (name, fire_ns) for name, (fire_ns, _, _) in self._timers.items() if fire_ns <= to_ns
            ]
            if not candidates:
                break
            candidates.sort(key=lambda x: x[1])
            name, fire_ns = candidates[0]
            _, interval_ns, cb = self._timers[name]

            self._now_ns = fire_ns
            evt = TimeEvent(name=name, ts_event=fire_ns, ts_init=fire_ns)
            cb(evt)
            triggered.append(evt)

            self._timers[name] = (fire_ns + interval_ns, interval_ns, cb)

        self._now_ns = to_ns
        return triggered

    def set_timer(self, name: str, interval_ns: int, callback: TimerCallback) -> None:
        if interval_ns <= 0:
            raise ValueError(f"interval_ns must be positive, got {interval_ns}")
        self._timers[name] = (self._now_ns + interval_ns, interval_ns, callback)

    def cancel_timer(self, name: str) -> None:
        self._timers.pop(name, None)


# ─── LiveClock ───


class LiveClock(Clock):
    """实盘时钟。

    ``now_ns`` 直接读系统时钟。定时器需要事件循环驱动 —— D-4 阶段未集成 asyncio，
    调 ``set_timer`` 会抛 ``NotImplementedError``。D-5 起在 ``Engine`` 内挂载
    asyncio loop 时再补完整实现。
    """

    def __init__(self) -> None:
        pass

    def now_ns(self) -> int:
        return time.time_ns()

    def set_timer(self, name: str, interval_ns: int, callback: TimerCallback) -> None:
        raise NotImplementedError(
            "LiveClock.set_timer requires asyncio loop, attached in Engine (D-5)"
        )

    def cancel_timer(self, name: str) -> None:
        # D-5 起会实现，目前 noop（保持接口对称）
        pass
