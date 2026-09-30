"""Approved manual plans respect reserved cash and the main position book."""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import pytest
import respx
from fastapi.testclient import TestClient
from httpx import Response

from inalpha_paper.account_id import account_id_from_sub

from .conftest import fresh_account_token
from .test_run_wallet_isolation import book, fill

pytestmark = pytest.mark.integration


def _approve(
    client: TestClient, headers: dict[str, str], side: str, quantity: float
) -> tuple[str, str]:
    created = client.post(
        "/plans",
        headers=headers,
        json={
            "intent": "open_long" if side == "BUY" else "close",
            "venue": "binance",
            "symbol": "BTC/USDT",
            "side": side,
            "type": "MARKET",
            "quantity": quantity,
            "rationale": "Verify main-book cash and position reservation",
            "expire_in_seconds": 300,
        },
    )
    assert created.status_code == 200, created.text
    plan_id = created.json()["plan_id"]
    approved = client.post(
        f"/plans/{plan_id}/approve", headers=headers, json={"approver": "tester"}
    )
    assert approved.status_code == 200, approved.text
    return plan_id, approved.json()["approval_token"]


def _execute(client: TestClient, headers: dict[str, str], approved: tuple[str, str]) -> Any:
    plan_id, token = approved
    return client.post(f"/plans/{plan_id}/execute", headers=headers, json={"approvalToken": token})


def _ticker() -> None:
    respx.get("http://data-mock.test/ticker").mock(
        return_value=Response(
            200,
            json={
                "venue": "binance",
                "symbol": "BTC/USDT",
                "price": 100,
                "ts": "2026-09-30T00:00:00Z",
                "source": "binance_ticker",
                "is_stale": False,
                "stale_seconds": 0,
            },
        )
    )


@respx.mock
def test_plan_approved_before_allocation_cannot_spend_wallet_capital_and_token_retries(
    client: TestClient,
) -> None:
    _ticker()
    sub, token = fresh_account_token("reserved-plan-cash")
    headers = {"Authorization": f"Bearer {token}"}
    approved = _approve(client, headers, "BUY", 1)
    asyncio.run(book(account_id_from_sub(sub), "10000"))
    rejected = _execute(client, headers, approved)
    assert rejected.status_code == 409, rejected.text
    assert rejected.json()["code"] == "INSUFFICIENT_CASH"
    pending = client.get(f"/plans/{approved[0]}", headers=headers).json()
    assert pending["status"] == "approved"
    assert pending["resulting_order_id"] is None
    assert client.get("/orders", headers=headers).json() == []
    deposited = client.post("/accounts/me/deposit", headers=headers, json={"amount": 200})
    assert deposited.status_code == 200, deposited.text
    executed = _execute(client, headers, approved)
    assert executed.status_code == 200, executed.text
    assert executed.json()["order"]["status"] == "FILLED"


@respx.mock
def test_sell_plan_cannot_use_same_symbol_run_position(client: TestClient) -> None:
    _ticker()
    sub, token = fresh_account_token("reserved-plan-position")
    headers = {"Authorization": f"Bearer {token}"}
    owner = account_id_from_sub(sub)
    run = asyncio.run(book(owner))
    asyncio.run(fill(owner, run, "BUY", "1", "100"))
    approved = _approve(client, headers, "SELL", 1)
    rejected = _execute(client, headers, approved)
    assert rejected.status_code == 409, rejected.text
    assert rejected.json()["code"] == "INSUFFICIENT_POSITION"
    assert client.get("/positions", headers=headers).json() == []
    positions = client.get(f"/strategy_runs/{run['id']}/positions", headers=headers).json()
    assert float(positions[0]["quantity"]) == 1
    assert client.get(f"/plans/{approved[0]}", headers=headers).json()["status"] == "approved"
    asyncio.run(fill(owner, None, "BUY", "1", "100"))
    executed = _execute(client, headers, approved)
    assert executed.status_code == 200, executed.text
    assert client.get("/positions", headers=headers).json() == []
    positions = client.get(f"/strategy_runs/{run['id']}/positions", headers=headers).json()
    assert float(positions[0]["quantity"]) == 1


@respx.mock
def test_concurrent_buy_plans_cannot_overdraw_remaining_main_cash(client: TestClient) -> None:
    _ticker()
    sub, token = fresh_account_token("concurrent-plan-cash")
    headers = {"Authorization": f"Bearer {token}"}
    asyncio.run(book(account_id_from_sub(sub), "9850"))
    approved = [_approve(client, headers, "BUY", 1) for _ in range(2)]
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda plan: _execute(client, headers, plan), approved))
    assert sorted(r.status_code for r in responses) == [200, 409]
    assert len(client.get("/orders", headers=headers).json()) == 1
    assert len(client.get("/positions", headers=headers).json()) == 1


@respx.mock
def test_plan_refuses_stale_fx_without_consuming_approval(client: TestClient) -> None:
    from inalpha_shared.db import get_conn

    from inalpha_paper.storage import accounts

    _ticker()
    sub, token = fresh_account_token("stale-plan-fx")
    headers = {"Authorization": f"Bearer {token}"}
    owner = account_id_from_sub(sub)

    async def add_currency() -> None:
        async with get_conn() as conn:
            await accounts.get_or_create(conn, owner)
            await accounts.apply_cash_delta(conn, owner, 100, currency="HKD")

    asyncio.run(add_currency())
    respx.get("http://data-mock.test/fx", params={"base": "HKD", "quote": "USD"}).mock(
        return_value=Response(200, json={"rate": 0.13, "is_stale": True})
    )
    approved = _approve(client, headers, "BUY", 1)
    rejected = _execute(client, headers, approved)
    assert rejected.status_code == 409, rejected.text
    assert client.get("/orders", headers=headers).json() == []
    assert client.get(f"/plans/{approved[0]}", headers=headers).json()["status"] == "approved"
