"""成分快照调度器（ADR-0053 阶段 C）：配置解析 + 幂等 tick（无 DB/无网络）。"""
from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime

import pytest

from inalpha_data import scheduler as sched
from inalpha_data.scheduler import (
    ConstituentSnapshotScheduler,
    parse_indices,
)


def test_parse_indices_strips_dedups_keeps_order() -> None:
    """逗号分隔解析：去空白、去重保序、空项剔除。"""
    assert parse_indices(" 000300 , 000905 ,000300, ") == ["000300", "000905"]
    assert parse_indices("") == []
    assert parse_indices("  ,  ") == []


async def test_empty_indices_disables_scheduler() -> None:
    """无追踪指数 → start() 不起任务（调度禁用）。"""
    s = ConstituentSnapshotScheduler(index_codes=[], interval_s=1.0)
    s.start()
    assert s._task is None
    await s.stop()  # 无任务时 stop 安全


async def test_tick_skips_when_today_snapshot_exists(monkeypatch: pytest.MonkeyPatch) -> None:
    """今天已有快照 → 跳过，不重复打源站（幂等）。"""
    today = datetime.now(UTC).date()

    @asynccontextmanager
    async def fake_conn():  # type: ignore[no-untyped-def]
        yield object()

    monkeypatch.setattr(sched, "get_conn", fake_conn)
    monkeypatch.setattr(
        sched.store, "get_constituents",
        lambda *a, **k: _coro((today, [{"code": "sh.600000"}])),
    )
    calls: list[str] = []
    monkeypatch.setattr(
        sched, "record_snapshot",
        lambda db, *, index_code: _coro(_record_marker(calls, index_code)),
    )

    s = ConstituentSnapshotScheduler(index_codes=["000300"], interval_s=1.0)
    await s._tick()
    assert calls == []  # 今天已有 → 未触发拉取


async def test_tick_records_when_no_snapshot_today(monkeypatch: pytest.MonkeyPatch) -> None:
    """今天没快照 → 调 record_snapshot 补当天。"""

    @asynccontextmanager
    async def fake_conn():  # type: ignore[no-untyped-def]
        yield object()

    monkeypatch.setattr(sched, "get_conn", fake_conn)
    monkeypatch.setattr(
        sched.store, "get_constituents", lambda *a, **k: _coro((None, []))
    )
    calls: list[str] = []
    monkeypatch.setattr(
        sched, "record_snapshot",
        lambda db, *, index_code: _coro(_record_marker(calls, index_code)),
    )

    s = ConstituentSnapshotScheduler(index_codes=["000300", "000905"], interval_s=1.0)
    await s._tick()
    assert calls == ["000300", "000905"]  # 两个指数都补


async def test_tick_isolates_per_index_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """单指数拉取失败只跳过该指数，不拖垮其余。"""

    @asynccontextmanager
    async def fake_conn():  # type: ignore[no-untyped-def]
        yield object()

    monkeypatch.setattr(sched, "get_conn", fake_conn)
    monkeypatch.setattr(
        sched.store, "get_constituents", lambda *a, **k: _coro((None, []))
    )
    calls: list[str] = []

    async def flaky_record(db, *, index_code):  # type: ignore[no-untyped-def]
        if index_code == "BAD":
            raise RuntimeError("akshare 源站失败")
        calls.append(index_code)

    monkeypatch.setattr(sched, "record_snapshot", flaky_record)

    s = ConstituentSnapshotScheduler(index_codes=["BAD", "000300"], interval_s=1.0)
    await s._tick()  # 不抛
    assert calls == ["000300"]  # 坏指数跳过，好指数照常


# ── helpers ──────────────────────────────────────────────────────────


async def _coro(value):  # type: ignore[no-untyped-def]
    return value


def _record_marker(calls: list[str], index_code: str) -> tuple[str, int]:
    calls.append(index_code)
    return "2026-06-26", 1
