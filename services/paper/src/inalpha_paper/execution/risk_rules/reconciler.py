"""`LockStoreReconciler` —— InMemoryLockStore → PostgreSQL `risk_locks` 周期同步。

[ADR-0006 §D3](../../../../../docs/miro/decisions/0006-risk-rules.md) 设计的 sync→async
桥接。RiskEngine 同步路径写 InMemoryLockStore；本 reconciler 异步把 dirty 状态
dump 到 DB 让 API `/risk/locks` + 外部审计能看到。

工作模式：

- ``reconcile_once()``：单次 dump（pending_insert → INSERT，pending_unlock → UPDATE，
  到期 lock → expire_past_locks）。**幂等**：再次跑且无 dirty 则零写入。
- ``run_forever(stop_event)``：后台 task 主循环，每 ``interval_seconds`` 跑一次。

接入：

```python
# lifespan startup
reconciler = LockStoreReconciler(store, pool, interval_seconds=5.0)
stop = asyncio.Event()
task = asyncio.create_task(reconciler.run_forever(stop))

# lifespan shutdown
stop.set()
await task
```

**当前架构限制**：每个 BacktestEngine 自管一个 InMemoryLockStore，没有 service 全局
长寿命 store。本 reconciler 适合未来实盘/dry-run 持续运行的 RiskEngine。

测试 mock 时把 `_db_pool` 替换为支持 async with 的 mock（参考 tests/test_risk_reconciler.py）。
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from ..risk_rules.lock_store import InMemoryLockStore

if TYPE_CHECKING:
    from psycopg_pool import AsyncConnectionPool

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ReconcileStats:
    inserted: int = 0
    unlocked: int = 0
    expired: int = 0

    @property
    def total_changes(self) -> int:
        return self.inserted + self.unlocked + self.expired


class LockStoreReconciler:
    """周期性把 InMemoryLockStore 状态同步到 PostgreSQL `risk_locks` 表。"""

    def __init__(
        self,
        store: InMemoryLockStore,
        db_pool: AsyncConnectionPool,
        *,
        interval_seconds: float = 5.0,
    ) -> None:
        if interval_seconds <= 0:
            raise ValueError(f"interval_seconds must be positive, got {interval_seconds}")
        self._store = store
        self._db_pool = db_pool
        self._interval = interval_seconds

    async def reconcile_once(self) -> ReconcileStats:
        """单次同步。幂等：无 dirty 时零写入。"""
        from ...storage import risk_locks as locks_store

        now = datetime.now(UTC)
        inserted = 0
        unlocked = 0

        # 1. dirty inserts → INSERT
        pending_inserts = self._store.get_pending_inserts()
        if pending_inserts:
            async with self._db_pool.connection() as conn:
                for lock in pending_inserts:
                    db_id = await locks_store.insert(
                        conn,
                        scope=lock.scope,
                        rule_name=lock.rule_name,
                        reason=lock.reason,
                        locked_until=lock.locked_until,
                        market=lock.market,
                        symbol=lock.symbol,
                        side=lock.side,
                    )
                    self._store.mark_synced_insert(lock.id, db_id)
                    inserted += 1
                await conn.commit()

        # 2. dirty unlocks → UPDATE active=FALSE
        pending_unlocks = self._store.get_pending_unlocks()
        if pending_unlocks:
            async with self._db_pool.connection() as conn:
                for in_mem_id in pending_unlocks:
                    db_id = self._store.get_db_id(in_mem_id)
                    if db_id is None:
                        # 极端情况：unlock 比 insert 先 sync（不应发生）
                        logger.warning(
                            "reconciler: in-memory lock %d unlocked but no DB id mapping",
                            in_mem_id,
                        )
                        self._store.mark_synced_unlock(in_mem_id)
                        continue
                    ok = await locks_store.manual_unlock(
                        conn,
                        db_id,
                        unlocked_by="reconciler:in-memory-unlock",
                        unlock_reason="synced from InMemoryLockStore.manual_unlock",
                    )
                    if ok:
                        unlocked += 1
                    self._store.mark_synced_unlock(in_mem_id)
                await conn.commit()

        # 3. 过期 lock → active=FALSE（解决 reconciler 没死但 in-mem unlock 漏掉的情况）
        async with self._db_pool.connection() as conn:
            expired = await locks_store.expire_past_locks(conn, now=now)
            await conn.commit()

        return ReconcileStats(inserted=inserted, unlocked=unlocked, expired=expired)

    async def run_forever(self, stop_event: asyncio.Event) -> None:
        """后台主循环。`stop_event.set()` 后 graceful 退出。"""
        logger.info(
            "LockStoreReconciler started (interval=%.1fs)", self._interval
        )
        try:
            while not stop_event.is_set():
                try:
                    stats = await self.reconcile_once()
                    if stats.total_changes > 0:
                        logger.info(
                            "reconcile: inserted=%d unlocked=%d expired=%d",
                            stats.inserted, stats.unlocked, stats.expired,
                        )
                except Exception:
                    logger.exception("reconcile_once failed; will retry next tick")
                try:
                    await asyncio.wait_for(stop_event.wait(), timeout=self._interval)
                except TimeoutError:
                    pass
        finally:
            logger.info("LockStoreReconciler stopped")
