"""`LockStoreReconciler` + `InMemoryLockStore` dirty 跟踪。

单元：dirty 跟踪逻辑（无 DB 依赖）。
Integration（pytest.mark.integration）：真 DB 端到端同步路径。
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import inalpha_shared.db as shared_db
import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from inalpha_shared.db import get_conn

from inalpha_paper.execution.risk_rules import (
    InMemoryLockStore,
    LockStoreReconciler,
    RiskVerdict,
)
from inalpha_paper.kernel.identifiers import InstrumentId

# ─── helpers ───


def _btc() -> InstrumentId:
    return InstrumentId(symbol="BTC/USDT", venue="binance")


def _verdict(until: datetime, *, scope: str = "symbol", rule: str = "FakeRule") -> RiskVerdict:
    return RiskVerdict(
        until=until,
        reason="测试",
        rule_name=rule,
        lock_side="*",
        lock_scope=scope,  # type: ignore[arg-type]
    )


# ─── Dirty 跟踪（无 DB 依赖）───


def test_add_marks_pending_insert() -> None:
    store = InMemoryLockStore()
    now = datetime(2026, 5, 26, 12, 0, tzinfo=UTC)
    until = now + timedelta(hours=1)
    lock = store.add(_verdict(until), instrument_id=_btc(), now=now)

    pending = store.get_pending_inserts()
    assert len(pending) == 1
    assert pending[0].id == lock.id


def test_mark_synced_clears_pending() -> None:
    store = InMemoryLockStore()
    now = datetime(2026, 5, 26, 12, 0, tzinfo=UTC)
    lock = store.add(_verdict(now + timedelta(hours=1)), instrument_id=_btc(), now=now)

    store.mark_synced_insert(lock.id, db_id=999)
    assert store.get_pending_inserts() == []
    assert store.get_db_id(lock.id) == 999


def test_unlock_before_sync_clears_pending_insert() -> None:
    """add → unlock 立即（在 reconciler 跑之前）→ 应该不进 pending_unlock（DB 都没记）。"""
    store = InMemoryLockStore()
    now = datetime(2026, 5, 26, 12, 0, tzinfo=UTC)
    lock = store.add(_verdict(now + timedelta(hours=1)), instrument_id=_btc(), now=now)

    store.manual_unlock(lock.id, unlocked_by="x", unlock_reason="x")
    assert store.get_pending_inserts() == []
    assert store.get_pending_unlocks() == []


def test_unlock_after_sync_marks_pending_unlock() -> None:
    """add → reconciler sync → unlock → 应进 pending_unlock。"""
    store = InMemoryLockStore()
    now = datetime(2026, 5, 26, 12, 0, tzinfo=UTC)
    lock = store.add(_verdict(now + timedelta(hours=1)), instrument_id=_btc(), now=now)

    # 模拟 reconciler 把 lock sync 到 DB（拿到 DB id 999）
    store.mark_synced_insert(lock.id, db_id=999)

    store.manual_unlock(lock.id, unlocked_by="x", unlock_reason="x")
    assert store.get_pending_unlocks() == [lock.id]


def test_mark_synced_unlock_clears_pending() -> None:
    store = InMemoryLockStore()
    now = datetime(2026, 5, 26, 12, 0, tzinfo=UTC)
    lock = store.add(_verdict(now + timedelta(hours=1)), instrument_id=_btc(), now=now)

    store.mark_synced_insert(lock.id, db_id=999)
    store.manual_unlock(lock.id, unlocked_by="x", unlock_reason="x")
    assert store.get_pending_unlocks() == [lock.id]

    store.mark_synced_unlock(lock.id)
    assert store.get_pending_unlocks() == []


# ─── reconcile_once integration（真 DB） ───


pytestmark_integration = pytest.mark.integration


@pytest_asyncio.fixture
async def clean_risk_locks(client: TestClient) -> AsyncIterator[None]:
    """清空 risk_locks 表（依赖 client 拉起 DB pool）。"""
    del client
    async with get_conn() as conn:
        await conn.execute("DELETE FROM risk_locks")
        await conn.commit()
    yield
    async with get_conn() as conn:
        await conn.execute("DELETE FROM risk_locks")
        await conn.commit()


def _get_pool() -> Any:
    """从 inalpha_shared.db 拿全局 pool（lifespan 内已 init）。"""
    if shared_db._pool is None:
        raise RuntimeError("DB pool not initialized")
    return shared_db._pool


@pytest.mark.integration
@pytest.mark.asyncio
async def test_reconcile_inserts_pending_locks(clean_risk_locks: None) -> None:
    del clean_risk_locks
    from inalpha_paper.storage import risk_locks as locks_store

    store = InMemoryLockStore()
    now = datetime(2026, 5, 26, 12, 0, tzinfo=UTC)
    until = now + timedelta(hours=1)
    store.add(_verdict(until, rule="R-a"), instrument_id=_btc(), now=now)
    store.add(_verdict(until, scope="global", rule="R-b"), instrument_id=None, now=now)

    reconciler = LockStoreReconciler(store, _get_pool(), interval_seconds=1.0)
    stats = await reconciler.reconcile_once()

    assert stats.inserted == 2
    assert store.get_pending_inserts() == []
    # 第二次跑没 dirty
    stats2 = await reconciler.reconcile_once()
    assert stats2.inserted == 0

    # DB 真有 2 行
    async with get_conn() as conn:
        rows = await locks_store.list_active(conn, now=now)
    assert len(rows) == 2


@pytest.mark.integration
@pytest.mark.asyncio
async def test_reconcile_unlocks_dirty_after_sync(clean_risk_locks: None) -> None:
    del clean_risk_locks
    from inalpha_paper.storage import risk_locks as locks_store

    store = InMemoryLockStore()
    now = datetime(2026, 5, 26, 12, 0, tzinfo=UTC)
    lock = store.add(_verdict(now + timedelta(hours=1)), instrument_id=_btc(), now=now)

    reconciler = LockStoreReconciler(store, _get_pool(), interval_seconds=1.0)
    # 1. 先 sync insert
    await reconciler.reconcile_once()
    db_id = store.get_db_id(lock.id)
    assert db_id is not None

    # 2. in-memory unlock
    store.manual_unlock(lock.id, unlocked_by="x", unlock_reason="x")
    assert store.get_pending_unlocks() == [lock.id]

    # 3. reconcile 把 unlock 同步到 DB
    stats = await reconciler.reconcile_once()
    assert stats.unlocked == 1

    # DB 该 lock 应 active=FALSE
    async with get_conn() as conn:
        rows = await locks_store.list_active(conn, now=now)
    assert rows == []


@pytest.mark.integration
@pytest.mark.asyncio
async def test_reconcile_expires_past_locks(clean_risk_locks: None) -> None:
    """到 locked_until 的 lock 即使没 manual_unlock，reconciler 也自动 expire。"""
    del clean_risk_locks
    from inalpha_paper.storage import risk_locks as locks_store

    now = datetime.now(UTC)
    # 直接 DB insert 一个**已过期**的 lock（不走 InMemoryLockStore，模拟外部数据残留）
    async with get_conn() as conn:
        await locks_store.insert(
            conn,
            scope="global",
            rule_name="ExpiredLeftover",
            reason="过期残留",
            locked_until=now - timedelta(minutes=5),
        )
        await conn.commit()

    store = InMemoryLockStore()
    reconciler = LockStoreReconciler(store, _get_pool(), interval_seconds=1.0)
    stats = await reconciler.reconcile_once()

    assert stats.expired >= 1


# ─── run_forever 控制循环 ───


@pytest.mark.integration
@pytest.mark.asyncio
async def test_run_forever_stops_on_event(clean_risk_locks: None) -> None:
    del clean_risk_locks
    store = InMemoryLockStore()
    reconciler = LockStoreReconciler(store, _get_pool(), interval_seconds=0.05)
    stop = asyncio.Event()

    task = asyncio.create_task(reconciler.run_forever(stop))
    await asyncio.sleep(0.15)  # 跑 2-3 个 tick
    stop.set()
    await asyncio.wait_for(task, timeout=2.0)
    assert task.done()


def test_interval_must_be_positive() -> None:
    store = InMemoryLockStore()
    with pytest.raises(ValueError, match="interval_seconds"):
        LockStoreReconciler(store, db_pool=None, interval_seconds=0)  # type: ignore[arg-type]
