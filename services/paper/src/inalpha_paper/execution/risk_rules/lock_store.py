"""`LockStore` —— RiskEngine 命中 verdict 后的锁持久化。

[ADR-0006 §D3](../../../../../docs/miro/decisions/0006-risk-rules.md) 设计：

- **接口 sync**：RiskEngine `_handle` 是 msgbus 同步 callback，不能 ``await``
- **InMemoryLockStore**：默认实现，dict-based，零依赖，**RiskEngine 直接调**
- **PostgreSQL 持久化**走异步路径（[`storage/risk_locks.py`](../../storage/risk_locks.py)），
  由后台 reconcile worker 定期把 InMemory state dump 进 DB。**不在本 Slice 范围**。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable

from ...kernel.identifiers import InstrumentId
from .base import LockScope, RiskVerdict, Side


@dataclass(frozen=True, slots=True)
class ActiveLock:
    """`risk_locks` 表一行的 in-memory 等价物。"""

    id: int
    scope: LockScope
    market: str | None
    symbol: str | None
    side: Side
    rule_name: str
    reason: str
    locked_at: datetime
    locked_until: datetime


@runtime_checkable
class LockStore(Protocol):
    """RiskEngine 持久化 verdict 的接口。sync（msgbus callback 限制）。"""

    def add(
        self,
        verdict: RiskVerdict,
        *,
        instrument_id: InstrumentId | None,
        now: datetime,
    ) -> ActiveLock:
        """RiskRule 命中后写一条 lock，返回带 id 的 ActiveLock。"""
        ...

    def list_active(
        self,
        now: datetime,
        *,
        scope: LockScope | None = None,
    ) -> list[ActiveLock]:
        """列 ``now`` 仍生效的 lock。按 locked_until DESC 排。"""
        ...

    def is_locked(
        self,
        now: datetime,
        *,
        scope: LockScope,
        market: str | None = None,
        symbol: str | None = None,
        side: Side = "*",
    ) -> ActiveLock | None:
        """查指定范围是否被锁。命中返回 first ActiveLock，否则 None。"""
        ...

    def manual_unlock(
        self,
        lock_id: int,
        *,
        unlocked_by: str,
        unlock_reason: str,
    ) -> bool:
        """人工 unlock。返回是否真的有 lock 被解（false = id 不存在或已 inactive）。"""
        ...


class InMemoryLockStore:
    """单进程内存 LockStore。dict-based + 自增 id。

    线程不安全（Inalpha msgbus 单线程模式 OK）。
    多进程 / 跨服务场景必须用 PostgreSQL 实现替换。

    Dirty 跟踪（reconcile worker 用）：

    - `_pending_insert`：add 后未 sync 到 DB 的 lock id
    - `_pending_unlock`：manual_unlock 后未 sync 到 DB 的 lock id
    - 调 `mark_synced_*()` 在 reconcile 后清掉

    本类**不知道** DB 存在 —— 仅暴露状态接口；`LockStoreReconciler` 拉数据写 DB。
    """

    def __init__(self) -> None:
        self._locks: dict[int, ActiveLock] = {}
        self._inactive: set[int] = set()
        self._next_id = 1
        # dirty 跟踪：reconcile worker 周期性拉走并 mark synced
        self._pending_insert: set[int] = set()
        self._pending_unlock: set[int] = set()
        # 当 lock 已 sync 进 DB 后，记录其 in-memory id → DB id 映射（外部 reconciler 填）
        self._db_id_map: dict[int, int] = {}

    def add(
        self,
        verdict: RiskVerdict,
        *,
        instrument_id: InstrumentId | None,
        now: datetime,
    ) -> ActiveLock:
        lock_id = self._next_id
        self._next_id += 1
        market = verdict.lock_market
        symbol: str | None = None
        if verdict.lock_scope == "symbol" and instrument_id is not None:
            symbol = str(instrument_id)
            if market is None:
                market = instrument_id.venue
        lock = ActiveLock(
            id=lock_id,
            scope=verdict.lock_scope,
            market=market,
            symbol=symbol,
            side=verdict.lock_side,
            rule_name=verdict.rule_name,
            reason=verdict.reason,
            locked_at=now,
            locked_until=verdict.until,
        )
        self._locks[lock_id] = lock
        self._pending_insert.add(lock_id)
        return lock

    def list_active(
        self,
        now: datetime,
        *,
        scope: LockScope | None = None,
    ) -> list[ActiveLock]:
        out = [
            lock
            for lock_id, lock in self._locks.items()
            if lock_id not in self._inactive and lock.locked_until > now
        ]
        if scope is not None:
            out = [lock for lock in out if lock.scope == scope]
        return sorted(out, key=lambda lk: lk.locked_until, reverse=True)

    def is_locked(
        self,
        now: datetime,
        *,
        scope: LockScope,
        market: str | None = None,
        symbol: str | None = None,
        side: Side = "*",
    ) -> ActiveLock | None:
        for lock in self.list_active(now, scope=scope):
            if not _side_intersects(lock.side, side):
                continue
            if scope == "global":
                return lock
            if scope == "market" and lock.market == market:
                return lock
            if scope == "symbol" and lock.symbol == symbol:
                return lock
        return None

    def manual_unlock(
        self,
        lock_id: int,
        *,
        unlocked_by: str,
        unlock_reason: str,
    ) -> bool:
        del unlocked_by, unlock_reason  # in-memory 不存审计字段，PG 实现存
        if lock_id not in self._locks or lock_id in self._inactive:
            return False
        self._inactive.add(lock_id)
        # 同步还没跑过 → 直接从 pending_insert 删（DB 还没记，无需 unlock）
        # 已经 sync 过 → 进 pending_unlock（reconciler 下次 UPDATE active=FALSE）
        if lock_id in self._pending_insert:
            self._pending_insert.discard(lock_id)
        else:
            self._pending_unlock.add(lock_id)
        return True

    # ─── Reconcile worker 接口 ───

    def get_pending_inserts(self) -> list[ActiveLock]:
        """返回所有 dirty insert（已 add 但未 sync 到 DB）的 lock。"""
        return [self._locks[lid] for lid in sorted(self._pending_insert) if lid in self._locks]

    def get_pending_unlocks(self) -> list[int]:
        """返回所有 dirty unlock（已 manual_unlock 但未 sync 到 DB）的 in-memory lock id。"""
        return sorted(self._pending_unlock)

    def mark_synced_insert(self, in_memory_id: int, db_id: int) -> None:
        """reconciler INSERT 成功后调，记 in-memory_id → db_id 映射。"""
        self._pending_insert.discard(in_memory_id)
        self._db_id_map[in_memory_id] = db_id

    def mark_synced_unlock(self, in_memory_id: int) -> None:
        """reconciler UPDATE active=FALSE 成功后调。"""
        self._pending_unlock.discard(in_memory_id)

    def get_db_id(self, in_memory_id: int) -> int | None:
        return self._db_id_map.get(in_memory_id)


def _side_intersects(lock_side: Side, query_side: Side) -> bool:
    """`*` 锁拦所有方向；单边锁只拦同向或 `*` 查询。"""
    if lock_side == "*" or query_side == "*":
        return True
    return lock_side == query_side
