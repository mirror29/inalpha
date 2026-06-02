"""``storage.strategy_runs`` 测试（D-11）——UNIQUE running + reconcile。"""
from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest
from inalpha_shared.db import get_conn

from inalpha_paper.storage import strategy_runs as runs_store

pytestmark = pytest.mark.integration


async def _insert(candidate_id, account_id):  # type: ignore[no-untyped-def]
    async with get_conn() as conn:
        return await runs_store.insert(
            conn, candidate_id=candidate_id, account_id=account_id,
            venue="binance", symbol="BTC/USDT", timeframe="1h", params={"x": 1},
        )


async def test_insert_returns_running_row(app_with_lifespan: Any) -> None:
    run = await _insert(uuid4(), uuid4())
    assert run["status"] == "running"
    assert run["venue"] == "binance"
    assert run["params"] == {"x": 1}


async def test_unique_running_per_candidate(app_with_lifespan: Any) -> None:
    candidate_id = uuid4()
    await _insert(candidate_id, uuid4())
    # 同 candidate 第二个 running → 撞部分唯一索引 → StrategyRunConflict
    with pytest.raises(runs_store.StrategyRunConflict):
        await _insert(candidate_id, uuid4())


async def test_stopped_frees_the_candidate(app_with_lifespan: Any) -> None:
    """stop 后同 candidate 可重新 start（部分唯一索引只约束 running）。"""
    candidate_id = uuid4()
    run = await _insert(candidate_id, uuid4())
    async with get_conn() as conn:
        await runs_store.set_status(conn, run["id"], "stopped")
    # 再起一个 running 不应冲突
    run2 = await _insert(candidate_id, uuid4())
    assert run2["status"] == "running"


async def test_update_progress_and_error_log(app_with_lifespan: Any) -> None:
    run = await _insert(uuid4(), uuid4())
    from datetime import UTC, datetime
    async with get_conn() as conn:
        await runs_store.update_progress(
            conn, run["id"], last_bar_ts=datetime(2026, 6, 1, tzinfo=UTC),
            cumulative_pnl=Decimal("12.5"),
        )
        await runs_store.append_error_log(conn, run["id"], "boom")
        fresh = await runs_store.get(conn, run["id"])
    assert fresh is not None
    assert Decimal(str(fresh["cumulative_pnl"])) == Decimal("12.5")
    assert len(fresh["error_log"]) == 1
    assert fresh["error_log"][0]["error"] == "boom"


async def test_mark_running_as_errored_reconcile(app_with_lifespan: Any) -> None:
    run = await _insert(uuid4(), uuid4())
    async with get_conn() as conn:
        n = await runs_store.mark_running_as_errored(conn, reason="service restarted")
        fresh = await runs_store.get(conn, run["id"])
    assert n >= 1
    assert fresh is not None
    assert fresh["status"] == "errored"
