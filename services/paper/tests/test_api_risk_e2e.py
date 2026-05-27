"""HTTP 端到端：POST /orders/submit + POST /plans/{id}/execute 接 RiskGuard 拦截。

测试策略：lifespan 加载的 risk_rules.toml 命中条件复杂（需要 trade_repo 真接），
直接 monkeypatch ``app.state.risk_guard`` 注入一个 always-fail mock guard，验证：

1. POST /orders/submit 命中 → 409 + body.code='RISK_REJECTED' + risk_locks 表新增
2. 第二次同条件 → 命中现有锁，仍 409
3. ``risk_guard=None``（disabled / 加载失败）→ pass-through，下单成功
4. POST /plans/{id}/execute 也走同样拦截
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from inalpha_shared.db import get_conn

from inalpha_paper.execution.risk_guard import RiskGuard
from inalpha_paper.execution.risk_rules import RiskRule
from inalpha_paper.execution.risk_rules.base import RiskVerdict, Side
from inalpha_paper.kernel.identifiers import InstrumentId
from inalpha_paper.storage import risk_locks as locks_store

pytestmark = pytest.mark.integration


class _NoopRepo:
    def get_closed_trades(self, **_: object) -> list:  # type: ignore[type-arg]
        return []


class _AlwaysFailSymbolRule(RiskRule):
    has_symbol_check = True

    def __init__(self) -> None:
        super().__init__({"stop_duration_min": 60}, _NoopRepo())  # type: ignore[arg-type]
        self._name = "TestStoplossGuard"

    @property
    def name(self) -> str:  # type: ignore[override]
        return self._name

    def short_desc(self) -> str:
        return "test rule that always fails"

    def check_symbol(
        self,
        instrument_id: InstrumentId,
        now: datetime,
        side: Side,
        starting_balance: float,
    ) -> RiskVerdict | None:
        return RiskVerdict(
            until=now + timedelta(hours=1),
            reason=f"test trigger on {instrument_id}",
            rule_name=self._name,
            lock_side=side,
            lock_scope="symbol",
        )


@pytest.fixture(autouse=True)
async def _truncate_risk_locks(app_with_lifespan):  # type: ignore[no-untyped-def]
    async with get_conn() as conn:
        async with conn.cursor() as cur:
            await cur.execute("TRUNCATE TABLE risk_locks RESTART IDENTITY")
    yield


@pytest.fixture
def failing_guard(app_with_lifespan: Any) -> RiskGuard:
    """注入一个 always-fail RiskGuard 到 app.state，覆盖 lifespan 默认加载。"""
    guard = RiskGuard(rules=[_AlwaysFailSymbolRule()], starting_balance=10_000.0)
    app_with_lifespan.state.risk_guard = guard
    return guard


# ────────────────────────────────────────────────────────────────────
# POST /orders/submit
# ────────────────────────────────────────────────────────────────────


def test_post_orders_submit_blocked_by_risk_rule(
    client: TestClient,
    auth_headers: dict[str, str],
    failing_guard: RiskGuard,
) -> None:
    r = client.post(
        "/orders/submit",
        headers=auth_headers,
        json={
            "symbol": "BTC/USDT",
            "venue": "binance",
            "side": "BUY",
            "type": "MARKET",
            "quantity": 0.01,
            "ref_price": 50_000.0,
        },
    )
    assert r.status_code == 409, r.json()
    body = r.json()
    assert body["code"] == "RISK_REJECTED"
    assert body["details"]["rule_name"] == "TestStoplossGuard"
    assert body["details"]["lock_scope"] == "symbol"
    assert body["details"]["from_existing_lock"] is False
    assert "locked_until" in body["details"]


async def test_post_orders_submit_second_call_hits_existing_lock(
    client: TestClient,
    auth_headers: dict[str, str],
    failing_guard: RiskGuard,
) -> None:
    """第一次拦截写锁 → 第二次同条件命中现有锁。"""
    payload = {
        "symbol": "BTC/USDT",
        "venue": "binance",
        "side": "BUY",
        "type": "MARKET",
        "quantity": 0.01,
        "ref_price": 50_000.0,
    }
    r1 = client.post("/orders/submit", headers=auth_headers, json=payload)
    assert r1.status_code == 409
    assert r1.json()["details"]["from_existing_lock"] is False

    r2 = client.post("/orders/submit", headers=auth_headers, json=payload)
    assert r2.status_code == 409
    assert r2.json()["details"]["from_existing_lock"] is True

    # 表里应该只有一行锁（第二次命中现有，不重复写）
    async with get_conn() as conn:
        rows = await locks_store.list_active(conn, now=datetime.now(UTC))
    assert len(rows) == 1


def test_post_orders_submit_passes_when_guard_disabled(
    client: TestClient,
    auth_headers: dict[str, str],
    app_with_lifespan: Any,
) -> None:
    """app.state.risk_guard=None（risk_engine_enabled=false / 加载失败）→ pass-through。"""
    app_with_lifespan.state.risk_guard = None

    r = client.post(
        "/orders/submit",
        headers=auth_headers,
        json={
            "symbol": "BTC/USDT",
            "venue": "binance",
            "side": "BUY",
            "type": "MARKET",
            "quantity": 0.01,
            "ref_price": 50_000.0,
        },
    )
    assert r.status_code == 200, r.json()
    assert r.json()["status"] == "FILLED"


def test_post_orders_submit_passes_when_no_rules(
    client: TestClient,
    auth_headers: dict[str, str],
    app_with_lifespan: Any,
) -> None:
    """rules=[] 的 RiskGuard 也是 pass-through。"""
    app_with_lifespan.state.risk_guard = RiskGuard(rules=[], starting_balance=10_000.0)

    r = client.post(
        "/orders/submit",
        headers=auth_headers,
        json={
            "symbol": "BTC/USDT",
            "venue": "binance",
            "side": "BUY",
            "type": "MARKET",
            "quantity": 0.01,
            "ref_price": 50_000.0,
        },
    )
    assert r.status_code == 200


# ────────────────────────────────────────────────────────────────────
# POST /plans/{id}/execute
# ────────────────────────────────────────────────────────────────────


def test_execute_plan_blocked_by_risk_rule(
    client: TestClient,
    auth_headers: dict[str, str],
    failing_guard: RiskGuard,
    app_with_lifespan: Any,
) -> None:
    """plan create + approve 后 execute 命中风控 → 409 + plan 状态不变（仍 approved）。"""
    # 1. create plan
    r_create = client.post(
        "/plans",
        headers=auth_headers,
        json={
            "intent": "open_long",
            "symbol": "BTC/USDT",
            "venue": "binance",
            "side": "BUY",
            "type": "MARKET",
            "quantity": 0.01,
            "rationale": "test risk reject path",
            "expire_in_seconds": 300,
        },
    )
    assert r_create.status_code == 200, r_create.json()
    plan_id = r_create.json()["plan_id"]

    # 2. approve
    r_approve = client.post(
        f"/plans/{plan_id}/approve",
        headers=auth_headers,
        json={"approver": "tester"},
    )
    assert r_approve.status_code == 200, r_approve.json()
    token = r_approve.json()["approval_token"]
    assert token

    # 3. execute → 应被 risk_guard 拦截
    r_exec = client.post(
        f"/plans/{plan_id}/execute",
        headers=auth_headers,
        json={"approvalToken": token},
    )
    assert r_exec.status_code == 409, r_exec.json()
    body = r_exec.json()
    assert body["code"] == "RISK_REJECTED"

    # 4. plan 状态应仍是 approved（未消费 token），用户可待锁释放后重试
    r_get = client.get(f"/plans/{plan_id}", headers=auth_headers)
    assert r_get.status_code == 200
    assert r_get.json()["status"] == "approved"
