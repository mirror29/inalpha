"""``storage/risk_locks.py`` 单测 —— 含本次新加的 ``is_locked``。

覆盖：

- ``insert`` 写一行返 id
- ``is_locked`` scope 优先级 / side intersect 语义
- ``list_active`` 按 active=TRUE + locked_until > now 过滤
- ``manual_unlock`` 软删
- ``expire_past_locks`` 批量过期
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from inalpha_shared.db import get_conn

from inalpha_paper.storage import risk_locks as locks_store

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
async def _truncate_risk_locks(app_with_lifespan):  # type: ignore[no-untyped-def]
    """每个 test 前清表，保证 isolation。依赖 app_with_lifespan 起 DB pool。"""
    async with get_conn() as conn:
        async with conn.cursor() as cur:
            await cur.execute("TRUNCATE TABLE risk_locks RESTART IDENTITY")
    yield


def _future(seconds: int) -> datetime:
    return datetime.now(UTC) + timedelta(seconds=seconds)


def _past(seconds: int) -> datetime:
    return datetime.now(UTC) - timedelta(seconds=seconds)


# ────────────────────────────────────────────────────────────────────
# insert
# ────────────────────────────────────────────────────────────────────


async def test_insert_returns_positive_id() -> None:
    async with get_conn() as conn:
        lock_id = await locks_store.insert(
            conn,
            scope="global",
            rule_name="MaxDrawdownRule",
            reason="账户回撤 20%",
            locked_until=_future(3600),
        )
    assert lock_id > 0


# ────────────────────────────────────────────────────────────────────
# is_locked
# ────────────────────────────────────────────────────────────────────


async def test_is_locked_returns_none_when_empty() -> None:
    async with get_conn() as conn:
        row = await locks_store.is_locked(
            conn, now=datetime.now(UTC), scope="global"
        )
    assert row is None


async def test_is_locked_matches_global_scope() -> None:
    async with get_conn() as conn:
        await locks_store.insert(
            conn,
            scope="global",
            rule_name="MaxDrawdownRule",
            reason="账户回撤 20%",
            locked_until=_future(3600),
        )
        row = await locks_store.is_locked(
            conn, now=datetime.now(UTC), scope="global"
        )
    assert row is not None
    assert row["scope"] == "global"
    assert row["rule_name"] == "MaxDrawdownRule"


async def test_is_locked_market_requires_market_arg() -> None:
    """scope='market' 不传 market 直接返 None（不会误命中其它锁）。"""
    async with get_conn() as conn:
        await locks_store.insert(
            conn,
            scope="market",
            rule_name="MarketHoursRule",
            reason="盘后",
            locked_until=_future(3600),
            market="nasdaq",
        )
        row = await locks_store.is_locked(
            conn, now=datetime.now(UTC), scope="market"
        )
    assert row is None


async def test_is_locked_market_matches_exact_market() -> None:
    async with get_conn() as conn:
        await locks_store.insert(
            conn,
            scope="market",
            rule_name="MarketHoursRule",
            reason="盘后",
            locked_until=_future(3600),
            market="nasdaq",
        )
        # 同 market → 命中
        row = await locks_store.is_locked(
            conn, now=datetime.now(UTC), scope="market", market="nasdaq"
        )
        assert row is not None
        # 不同 market → 不命中
        row2 = await locks_store.is_locked(
            conn, now=datetime.now(UTC), scope="market", market="binance"
        )
    assert row2 is None


async def test_is_locked_symbol_matches_exact_symbol() -> None:
    async with get_conn() as conn:
        await locks_store.insert(
            conn,
            scope="symbol",
            rule_name="CooldownRule",
            reason="刚平仓 5min 内",
            locked_until=_future(300),
            market="binance",
            symbol="BTC/USDT@binance",
        )
        row = await locks_store.is_locked(
            conn,
            now=datetime.now(UTC),
            scope="symbol",
            symbol="BTC/USDT@binance",
        )
        assert row is not None
        # 不同 symbol → 不命中
        row2 = await locks_store.is_locked(
            conn,
            now=datetime.now(UTC),
            scope="symbol",
            symbol="ETH/USDT@binance",
        )
    assert row2 is None


async def test_is_locked_side_intersect_star_blocks_long() -> None:
    """锁 side='*' 拦任何方向查询。"""
    async with get_conn() as conn:
        await locks_store.insert(
            conn,
            scope="global",
            rule_name="MaxDrawdownRule",
            reason="账户回撤",
            locked_until=_future(3600),
            side="*",
        )
        long_q = await locks_store.is_locked(
            conn, now=datetime.now(UTC), scope="global", side="long"
        )
        short_q = await locks_store.is_locked(
            conn, now=datetime.now(UTC), scope="global", side="short"
        )
    assert long_q is not None
    assert short_q is not None


async def test_is_locked_side_intersect_long_lock_does_not_block_short() -> None:
    """锁 side='long' 不拦 short 查询。"""
    async with get_conn() as conn:
        await locks_store.insert(
            conn,
            scope="global",
            rule_name="LowProfitRule",
            reason="long 连亏",
            locked_until=_future(3600),
            side="long",
        )
        long_q = await locks_store.is_locked(
            conn, now=datetime.now(UTC), scope="global", side="long"
        )
        short_q = await locks_store.is_locked(
            conn, now=datetime.now(UTC), scope="global", side="short"
        )
    assert long_q is not None  # long 锁拦 long 查询
    assert short_q is None  # long 锁不拦 short 查询


async def test_is_locked_expired_lock_ignored() -> None:
    """locked_until <= now → 不算 active。"""
    async with get_conn() as conn:
        await locks_store.insert(
            conn,
            scope="global",
            rule_name="MaxDrawdownRule",
            reason="过期了",
            locked_until=_past(1),
        )
        row = await locks_store.is_locked(
            conn, now=datetime.now(UTC), scope="global"
        )
    assert row is None


# ────────────────────────────────────────────────────────────────────
# list_active + manual_unlock + expire_past_locks
# ────────────────────────────────────────────────────────────────────


async def test_list_active_orders_by_locked_until_desc() -> None:
    async with get_conn() as conn:
        await locks_store.insert(
            conn,
            scope="global",
            rule_name="A",
            reason="r1",
            locked_until=_future(3600),
        )
        await locks_store.insert(
            conn,
            scope="global",
            rule_name="B",
            reason="r2",
            locked_until=_future(7200),
        )
        rows = await locks_store.list_active(conn, now=datetime.now(UTC))
    assert len(rows) == 2
    # 远先来
    assert rows[0]["rule_name"] == "B"
    assert rows[1]["rule_name"] == "A"


async def test_list_recent_includes_inactive_and_expired() -> None:
    """list_recent 含已过期 / 已解锁行（不按 active 过滤），按 locked_at DESC。"""
    async with get_conn() as conn:
        # 1) 生效中
        await locks_store.insert(
            conn,
            scope="global",
            rule_name="ActiveRule",
            reason="生效中",
            locked_until=_future(3600),
        )
        # 2) 已过期（locked_until 在过去）
        await locks_store.insert(
            conn,
            scope="symbol",
            rule_name="CooldownRule",
            reason="冷却过期",
            locked_until=_past(60),
            market="binance",
            symbol="BTC/USDT@binance",
        )
        # 3) 人工解锁（软删）
        unlocked_id = await locks_store.insert(
            conn,
            scope="market",
            rule_name="MarketHoursRule",
            reason="人工解",
            locked_until=_future(7200),
            market="nasdaq",
        )
        await locks_store.manual_unlock(
            conn, unlocked_id, unlocked_by="admin@test", unlock_reason="false positive"
        )

        rows = await locks_store.list_recent(conn, limit=50)

    # list_active 只会返 1 条（生效中）；list_recent 三条都在
    assert len(rows) == 3
    rule_names = {r["rule_name"] for r in rows}
    assert rule_names == {"ActiveRule", "CooldownRule", "MarketHoursRule"}
    # 带上状态元数据，调用方据此区分生效/过期/解锁
    by_rule = {r["rule_name"]: r for r in rows}
    assert by_rule["MarketHoursRule"]["active"] is False
    assert by_rule["MarketHoursRule"]["unlocked_by"] == "admin@test"
    assert by_rule["ActiveRule"]["active"] is True


async def test_list_recent_respects_limit() -> None:
    async with get_conn() as conn:
        for i in range(5):
            await locks_store.insert(
                conn,
                scope="global",
                rule_name=f"R{i}",
                reason="x",
                locked_until=_future(60),
            )
        rows = await locks_store.list_recent(conn, limit=2)
    assert len(rows) == 2


async def test_manual_unlock_soft_deletes() -> None:
    async with get_conn() as conn:
        lock_id = await locks_store.insert(
            conn,
            scope="global",
            rule_name="MaxDrawdownRule",
            reason="...",
            locked_until=_future(3600),
        )
        ok = await locks_store.manual_unlock(
            conn,
            lock_id,
            unlocked_by="admin@test",
            unlock_reason="false positive",
        )
        assert ok is True
        # 解锁后 list_active 应不再返回
        rows = await locks_store.list_active(conn, now=datetime.now(UTC))
    assert rows == []


async def test_manual_unlock_returns_false_for_unknown_id() -> None:
    async with get_conn() as conn:
        ok = await locks_store.manual_unlock(
            conn, 99999, unlocked_by="admin", unlock_reason="test"
        )
    assert ok is False


async def test_expire_past_locks_marks_inactive() -> None:
    async with get_conn() as conn:
        await locks_store.insert(
            conn,
            scope="global",
            rule_name="A",
            reason="past",
            locked_until=_past(60),
        )
        await locks_store.insert(
            conn,
            scope="global",
            rule_name="B",
            reason="future",
            locked_until=_future(60),
        )
        n = await locks_store.expire_past_locks(conn, now=datetime.now(UTC))
    assert n == 1  # 只有 A 被 expire
