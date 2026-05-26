"""``RiskGuard.check`` 端到端单测（不走 HTTP）。

测试矩阵：

- rules=[] → 永远返 None
- mock 一个 always-fail rule → check 命中 + risk_locks 表写一行
- 已有锁 → 第二次 check 同条件命中 ``from_existing_lock=True``，不写新行
- side 兼容：long 锁不拦 short 查询
- scope=symbol 命中时 instrument_id 解构到 market+symbol 列
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from inalpha_shared.db import get_conn

from inalpha_paper.execution.risk_guard import RiskGuard, RiskRejection
from inalpha_paper.execution.risk_rules import RiskRule
from inalpha_paper.execution.risk_rules.base import (
    RiskVerdict,
    Side,
)
from inalpha_paper.kernel.identifiers import InstrumentId
from inalpha_paper.storage import risk_locks as locks_store

pytestmark = pytest.mark.integration


class _NoopRepo:
    def get_closed_trades(self, **_: object) -> list:  # type: ignore[type-arg]
        return []


@pytest.fixture(autouse=True)
async def _truncate_risk_locks(app_with_lifespan):  # type: ignore[no-untyped-def]
    async with get_conn() as conn:
        async with conn.cursor() as cur:
            await cur.execute("TRUNCATE TABLE risk_locks RESTART IDENTITY")
    yield


# ────────────────────────────────────────────────────────────────────
# Mock rules（按 lock_scope 各一个）
# ────────────────────────────────────────────────────────────────────


class _AlwaysFailGlobalRule(RiskRule):
    """无脑 global 拦截，固定锁 1h、双向。"""

    has_global_check = True

    def __init__(self) -> None:
        super().__init__({"stop_duration_min": 60}, _NoopRepo())  # type: ignore[arg-type]
        self._name = "AlwaysFailGlobalRule"

    @property
    def name(self) -> str:  # type: ignore[override]
        return self._name

    def short_desc(self) -> str:
        return "test rule"

    def check_global(
        self, now: datetime, side: Side, starting_balance: float
    ) -> RiskVerdict | None:
        return RiskVerdict(
            until=now + timedelta(hours=1),
            reason="account-level test trigger",
            rule_name=self._name,
            lock_side="*",
            lock_scope="global",
        )


class _AlwaysFailSymbolRule(RiskRule):
    """无脑 symbol 拦截，固定锁 30min，方向跟随 cmd side。"""

    has_symbol_check = True

    def __init__(self) -> None:
        super().__init__({"stop_duration_min": 30}, _NoopRepo())  # type: ignore[arg-type]
        self._name = "AlwaysFailSymbolRule"

    @property
    def name(self) -> str:  # type: ignore[override]
        return self._name

    def short_desc(self) -> str:
        return "test rule"

    def check_symbol(
        self,
        instrument_id: InstrumentId,
        now: datetime,
        side: Side,
        starting_balance: float,
    ) -> RiskVerdict | None:
        return RiskVerdict(
            until=now + timedelta(minutes=30),
            reason=f"symbol test trigger on {instrument_id}",
            rule_name=self._name,
            lock_side=side,  # 按本次方向锁
            lock_scope="symbol",
        )


# ────────────────────────────────────────────────────────────────────
# Tests
# ────────────────────────────────────────────────────────────────────


_BTC = InstrumentId(symbol="BTC/USDT", venue="binance")


async def test_check_passes_when_no_rules() -> None:
    guard = RiskGuard(rules=[], starting_balance=10_000.0)
    async with get_conn() as conn:
        result = await guard.check(
            conn, instrument_id=_BTC, side="long", now=datetime.now(UTC)
        )
    assert result is None


async def test_global_rule_triggers_writes_lock_returns_rejection() -> None:
    guard = RiskGuard(rules=[_AlwaysFailGlobalRule()], starting_balance=10_000.0)
    async with get_conn() as conn:
        result = await guard.check(
            conn, instrument_id=_BTC, side="long", now=datetime.now(UTC)
        )
        assert isinstance(result, RiskRejection)
        assert result.from_existing_lock is False
        assert result.lock_scope == "global"
        assert "AlwaysFailGlobalRule" in result.rule_name

        # 表里应该多一行 global 锁
        rows = await locks_store.list_active(conn, now=datetime.now(UTC))
    assert len(rows) == 1
    assert rows[0]["scope"] == "global"
    assert rows[0]["market"] is None
    assert rows[0]["symbol"] is None


async def test_second_check_hits_existing_lock_no_new_row() -> None:
    """第一次 check 写锁 → 第二次同条件 check 命中现有锁，不写新行。"""
    guard = RiskGuard(rules=[_AlwaysFailGlobalRule()], starting_balance=10_000.0)
    async with get_conn() as conn:
        await guard.check(
            conn, instrument_id=_BTC, side="long", now=datetime.now(UTC)
        )
        second = await guard.check(
            conn, instrument_id=_BTC, side="long", now=datetime.now(UTC)
        )
        assert second is not None
        assert second.from_existing_lock is True

        rows = await locks_store.list_active(conn, now=datetime.now(UTC))
    assert len(rows) == 1  # 仍只有一行


async def test_symbol_rule_writes_lock_with_market_and_symbol() -> None:
    """scope='symbol' 命中时 market 列也填上（便于按 market 聚合查询）。"""
    guard = RiskGuard(rules=[_AlwaysFailSymbolRule()], starting_balance=10_000.0)
    async with get_conn() as conn:
        result = await guard.check(
            conn, instrument_id=_BTC, side="long", now=datetime.now(UTC)
        )
        assert result is not None
        assert result.lock_scope == "symbol"

        rows = await locks_store.list_active(conn, now=datetime.now(UTC))
    assert len(rows) == 1
    assert rows[0]["scope"] == "symbol"
    assert rows[0]["market"] == "binance"  # 从 instrument_id.venue 派生
    assert rows[0]["symbol"] == "BTC/USDT@binance"
    assert rows[0]["side"] == "long"  # 按本次方向锁


async def test_long_lock_does_not_block_short_check() -> None:
    """rule 写的 lock_side='long'，short 查询不应命中。"""
    guard = RiskGuard(rules=[_AlwaysFailSymbolRule()], starting_balance=10_000.0)
    async with get_conn() as conn:
        # long 触发，锁 long
        await guard.check(
            conn, instrument_id=_BTC, side="long", now=datetime.now(UTC)
        )
        # short 查询：不应命中现有锁，但 _AlwaysFailSymbolRule 会再触发锁 short
        result = await guard.check(
            conn, instrument_id=_BTC, side="short", now=datetime.now(UTC)
        )
        # short 触发新锁
        assert result is not None
        assert result.from_existing_lock is False  # 新触发，不是命中现有

        rows = await locks_store.list_active(conn, now=datetime.now(UTC))
    assert len(rows) == 2
    sides = {r["side"] for r in rows}
    assert sides == {"long", "short"}


async def test_rule_names_property() -> None:
    guard = RiskGuard(
        rules=[_AlwaysFailGlobalRule(), _AlwaysFailSymbolRule()],
        starting_balance=10_000.0,
    )
    assert guard.rule_count == 2
    assert "AlwaysFailGlobalRule" in guard.rule_names
    assert "AlwaysFailSymbolRule" in guard.rule_names
