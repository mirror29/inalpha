"""``POST /orders/submit`` API + ``OrderExecutor`` 单测。"""
from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from inalpha_paper.execution.order_executor import OrderExecutor

pytestmark = pytest.mark.integration


def _make_app() -> Any:
    from inalpha_paper.main import app

    return app


@pytest.fixture
def client() -> TestClient:
    return TestClient(_make_app())


# ─── OrderExecutor 纯函数单测 ───


def test_market_buy_fills_at_ref_price() -> None:
    r = OrderExecutor.execute(
        venue="binance",
        symbol="BTC/USDT",
        side="BUY",
        order_type="MARKET",
        quantity=0.01,
        price=None,
        ref_price=50_000.0,
        fee_rate=0.001,
    )
    assert r["status"] == "FILLED"
    assert r["avg_fill_price"] == 50_000.0
    assert r["filled_quantity"] == 0.01
    assert r["notional"] == 500.0
    assert r["fee"] == 0.5


def test_market_sell_fills_at_ref_price() -> None:
    r = OrderExecutor.execute(
        venue="binance",
        symbol="BTC/USDT",
        side="SELL",
        order_type="MARKET",
        quantity=0.02,
        price=None,
        ref_price=50_000.0,
        fee_rate=0.0,
    )
    assert r["status"] == "FILLED"
    assert r["fee"] == 0.0


def test_limit_buy_triggered_above_ref() -> None:
    # 限价 51000 买，参考价 50000 → 触发，成交于 min(51000, 50000) = 50000（保守）
    r = OrderExecutor.execute(
        venue="binance",
        symbol="BTC/USDT",
        side="BUY",
        order_type="LIMIT",
        quantity=0.01,
        price=51_000.0,
        ref_price=50_000.0,
        fee_rate=0.001,
    )
    assert r["status"] == "FILLED"
    assert r["avg_fill_price"] == 50_000.0


def test_limit_buy_not_triggered_below_ref() -> None:
    # 限价 49000 买，参考价 50000 → 不触发
    r = OrderExecutor.execute(
        venue="binance",
        symbol="BTC/USDT",
        side="BUY",
        order_type="LIMIT",
        quantity=0.01,
        price=49_000.0,
        ref_price=50_000.0,
        fee_rate=0.001,
    )
    assert r["status"] == "REJECTED"
    assert "not triggered" in str(r["rejection_reason"])


def test_limit_sell_triggered_below_ref() -> None:
    # 限价 49000 卖，参考价 50000 → 触发，成交于 max(49000, 50000) = 50000
    r = OrderExecutor.execute(
        venue="binance",
        symbol="BTC/USDT",
        side="SELL",
        order_type="LIMIT",
        quantity=0.01,
        price=49_000.0,
        ref_price=50_000.0,
        fee_rate=0.001,
    )
    assert r["status"] == "FILLED"
    assert r["avg_fill_price"] == 50_000.0


def test_client_order_id_is_unique() -> None:
    r1 = OrderExecutor.execute(
        venue="binance", symbol="BTC/USDT", side="BUY", order_type="MARKET",
        quantity=0.01, price=None, ref_price=50_000.0, fee_rate=0.001,
    )
    r2 = OrderExecutor.execute(
        venue="binance", symbol="BTC/USDT", side="BUY", order_type="MARKET",
        quantity=0.01, price=None, ref_price=50_000.0, fee_rate=0.001,
    )
    assert r1["client_order_id"] != r2["client_order_id"]


# ─── HTTP 路由测试 ───


def test_submit_order_requires_auth(client: TestClient) -> None:
    r = client.post(
        "/orders/submit",
        json={
            "symbol": "BTC/USDT",
            "side": "BUY",
            "type": "MARKET",
            "quantity": 0.01,
            "ref_price": 50_000.0,
        },
    )
    assert r.status_code == 401


def test_submit_market_order(client: TestClient, auth_headers: dict[str, str]) -> None:
    r = client.post(
        "/orders/submit",
        headers=auth_headers,
        json={
            "symbol": "BTC/USDT",
            "side": "BUY",
            "type": "MARKET",
            "quantity": 0.01,
            "ref_price": 50_000.0,
        },
    )
    assert r.status_code == 200, r.json()
    body = r.json()
    assert body["status"] == "FILLED"
    assert body["symbol"] == "BTC/USDT"
    assert body["side"] == "BUY"
    assert body["filled_quantity"] == 0.01
    assert body["avg_fill_price"] == 50_000.0
    assert body["client_order_id"].startswith("ord-")


def test_submit_limit_order_not_triggered(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    r = client.post(
        "/orders/submit",
        headers=auth_headers,
        json={
            "symbol": "BTC/USDT",
            "side": "BUY",
            "type": "LIMIT",
            "quantity": 0.01,
            "price": 49_000.0,
            "ref_price": 50_000.0,
        },
    )
    assert r.status_code == 200, r.json()
    body = r.json()
    assert body["status"] == "REJECTED"
    assert body["filled_quantity"] == 0.0


def test_submit_market_with_price_rejected(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """MARKET 不能带 price。"""
    r = client.post(
        "/orders/submit",
        headers=auth_headers,
        json={
            "symbol": "BTC/USDT",
            "side": "BUY",
            "type": "MARKET",
            "quantity": 0.01,
            "price": 50_000.0,
            "ref_price": 50_000.0,
        },
    )
    assert r.status_code == 400


def test_submit_limit_without_price_rejected(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """LIMIT 必须带 price。"""
    r = client.post(
        "/orders/submit",
        headers=auth_headers,
        json={
            "symbol": "BTC/USDT",
            "side": "BUY",
            "type": "LIMIT",
            "quantity": 0.01,
            "ref_price": 50_000.0,
        },
    )
    assert r.status_code == 400


def test_submit_negative_quantity_rejected(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    r = client.post(
        "/orders/submit",
        headers=auth_headers,
        json={
            "symbol": "BTC/USDT",
            "side": "BUY",
            "type": "MARKET",
            "quantity": -0.01,
            "ref_price": 50_000.0,
        },
    )
    assert r.status_code == 400
