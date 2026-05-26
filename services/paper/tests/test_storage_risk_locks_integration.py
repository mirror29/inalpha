"""`storage.risk_locks` PostgreSQL CRUD integration test。

依赖 alembic 0006 已在测试 DB 跑过（Step 1）。
测试用 conftest 现有 `app_with_lifespan` 拉起 DB pool，直接调 async CRUD。

覆盖：`insert` / `list_active`（4 种过滤）/ `manual_unlock` / `expire_past_locks`。
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from inalpha_shared.db import get_conn

from inalpha_paper.storage import risk_locks as locks_store

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def clean_risk_locks(client: TestClient) -> AsyncIterator[None]:
    """每个测试前清空 risk_locks 表。依赖 client fixture 拉起 DB pool。"""
    del client
    async with get_conn() as conn:
        await conn.execute("DELETE FROM risk_locks")
        await conn.commit()
    yield
    async with get_conn() as conn:
        await conn.execute("DELETE FROM risk_locks")
        await conn.commit()


# ─── insert ───


@pytest.mark.asyncio
async def test_insert_returns_id(clean_risk_locks: None) -> None:
    del clean_risk_locks
    until = datetime.now(UTC) + timedelta(hours=1)
    async with get_conn() as conn:
        lock_id = await locks_store.insert(
            conn,
            scope="symbol",
            rule_name="CooldownRule",
            reason="测试",
            locked_until=until,
            market="binance",
            symbol="BTC/USDT@binance",
            side="*",
        )
        await conn.commit()
    assert isinstance(lock_id, int)
    assert lock_id > 0


@pytest.mark.asyncio
async def test_insert_global_lock_without_market_symbol(clean_risk_locks: None) -> None:
    del clean_risk_locks
    until = datetime.now(UTC) + timedelta(hours=2)
    async with get_conn() as conn:
        lock_id = await locks_store.insert(
            conn,
            scope="global",
            rule_name="MaxDrawdownRule",
            reason="账户回撤 18%",
            locked_until=until,
        )
        await conn.commit()
    assert lock_id > 0


# ─── list_active ───


@pytest.mark.asyncio
async def test_list_active_excludes_expired(clean_risk_locks: None) -> None:
    """已过期的 lock 不返。"""
    del clean_risk_locks
    now = datetime.now(UTC)
    async with get_conn() as conn:
        # 未来 → 有效
        await locks_store.insert(
            conn, scope="symbol", rule_name="R1", reason="未来",
            locked_until=now + timedelta(hours=1),
            market="binance", symbol="BTC@binance",
        )
        # 过去 → 过期
        await locks_store.insert(
            conn, scope="symbol", rule_name="R2", reason="过去",
            locked_until=now - timedelta(hours=1),
            market="binance", symbol="ETH@binance",
        )
        await conn.commit()

    async with get_conn() as conn:
        rows = await locks_store.list_active(conn, now=now)
    assert len(rows) == 1
    assert rows[0]["rule_name"] == "R1"


@pytest.mark.asyncio
async def test_list_active_filter_by_scope(clean_risk_locks: None) -> None:
    del clean_risk_locks
    now = datetime.now(UTC)
    until = now + timedelta(hours=1)
    async with get_conn() as conn:
        await locks_store.insert(
            conn, scope="global", rule_name="R-g", reason="g", locked_until=until,
        )
        await locks_store.insert(
            conn, scope="market", rule_name="R-m", reason="m", locked_until=until,
            market="nasdaq",
        )
        await locks_store.insert(
            conn, scope="symbol", rule_name="R-s", reason="s", locked_until=until,
            market="binance", symbol="BTC@binance",
        )
        await conn.commit()

    async with get_conn() as conn:
        all_rows = await locks_store.list_active(conn, now=now)
        globals_only = await locks_store.list_active(conn, now=now, scope="global")
        symbols_only = await locks_store.list_active(conn, now=now, scope="symbol")

    assert len(all_rows) == 3
    assert len(globals_only) == 1
    assert globals_only[0]["rule_name"] == "R-g"
    assert len(symbols_only) == 1
    assert symbols_only[0]["rule_name"] == "R-s"


@pytest.mark.asyncio
async def test_list_active_filter_by_symbol(clean_risk_locks: None) -> None:
    del clean_risk_locks
    now = datetime.now(UTC)
    until = now + timedelta(hours=1)
    async with get_conn() as conn:
        await locks_store.insert(
            conn, scope="symbol", rule_name="R-btc", reason="btc",
            locked_until=until,
            market="binance", symbol="BTC@binance",
        )
        await locks_store.insert(
            conn, scope="symbol", rule_name="R-eth", reason="eth",
            locked_until=until,
            market="binance", symbol="ETH@binance",
        )
        await conn.commit()

    async with get_conn() as conn:
        rows = await locks_store.list_active(conn, now=now, symbol="BTC@binance")
    assert len(rows) == 1
    assert rows[0]["rule_name"] == "R-btc"


@pytest.mark.asyncio
async def test_list_active_orders_by_locked_until_desc(clean_risk_locks: None) -> None:
    """locked_until 远的排在前面（解锁时间最远的先看）。"""
    del clean_risk_locks
    now = datetime.now(UTC)
    async with get_conn() as conn:
        await locks_store.insert(
            conn, scope="global", rule_name="R-1h", reason="1h",
            locked_until=now + timedelta(hours=1),
        )
        await locks_store.insert(
            conn, scope="global", rule_name="R-4h", reason="4h",
            locked_until=now + timedelta(hours=4),
        )
        await locks_store.insert(
            conn, scope="global", rule_name="R-2h", reason="2h",
            locked_until=now + timedelta(hours=2),
        )
        await conn.commit()

    async with get_conn() as conn:
        rows = await locks_store.list_active(conn, now=now)
    rule_names = [r["rule_name"] for r in rows]
    assert rule_names == ["R-4h", "R-2h", "R-1h"]


# ─── manual_unlock ───


@pytest.mark.asyncio
async def test_manual_unlock_writes_audit_fields(clean_risk_locks: None) -> None:
    del clean_risk_locks
    until = datetime.now(UTC) + timedelta(hours=1)
    async with get_conn() as conn:
        lock_id = await locks_store.insert(
            conn, scope="symbol", rule_name="R", reason="x", locked_until=until,
            market="binance", symbol="BTC@binance",
        )
        await conn.commit()

    async with get_conn() as conn:
        ok = await locks_store.manual_unlock(
            conn, lock_id, unlocked_by="admin@inalpha", unlock_reason="正常解除"
        )
        await conn.commit()
    assert ok is True

    # 验证 active=FALSE + 审计字段写入
    async with get_conn() as conn, conn.cursor() as cur:
        await cur.execute(
            "SELECT active, unlocked_by, unlock_reason, unlocked_at "
            "FROM risk_locks WHERE id = %s",
            (lock_id,),
        )
        row = await cur.fetchone()
    assert row is not None
    row_data: dict[str, Any] = row  # type: ignore[assignment]
    assert row_data["active"] is False
    assert row_data["unlocked_by"] == "admin@inalpha"
    assert row_data["unlock_reason"] == "正常解除"
    assert row_data["unlocked_at"] is not None


@pytest.mark.asyncio
async def test_manual_unlock_returns_false_for_inactive(clean_risk_locks: None) -> None:
    """第二次 unlock 同一 id 返 False。"""
    del clean_risk_locks
    until = datetime.now(UTC) + timedelta(hours=1)
    async with get_conn() as conn:
        lock_id = await locks_store.insert(
            conn, scope="global", rule_name="R", reason="x", locked_until=until,
        )
        await conn.commit()

    async with get_conn() as conn:
        first = await locks_store.manual_unlock(
            conn, lock_id, unlocked_by="u", unlock_reason="r"
        )
        await conn.commit()
        second = await locks_store.manual_unlock(
            conn, lock_id, unlocked_by="u", unlock_reason="r"
        )
        await conn.commit()
    assert first is True
    assert second is False


# ─── expire_past_locks ───


@pytest.mark.asyncio
async def test_expire_past_locks_marks_inactive(clean_risk_locks: None) -> None:
    """到 locked_until 的 lock 自动 expire。"""
    del clean_risk_locks
    now = datetime.now(UTC)
    async with get_conn() as conn:
        # 已过期
        await locks_store.insert(
            conn, scope="global", rule_name="expired", reason="expired",
            locked_until=now - timedelta(minutes=10),
        )
        await locks_store.insert(
            conn, scope="global", rule_name="also-expired", reason="also-expired",
            locked_until=now - timedelta(minutes=1),
        )
        # 未来：不应被 expire
        await locks_store.insert(
            conn, scope="global", rule_name="future", reason="future",
            locked_until=now + timedelta(hours=1),
        )
        await conn.commit()

    async with get_conn() as conn:
        count = await locks_store.expire_past_locks(conn, now=now)
        await conn.commit()
    assert count == 2

    # 验证 future 仍 active
    async with get_conn() as conn:
        rows = await locks_store.list_active(conn, now=now)
    assert len(rows) == 1
    assert rows[0]["rule_name"] == "future"
