"""``POST /orders/submit`` API + ``OrderExecutor`` 单测。

D-8b 起：API 测试用 conftest 的 ``client`` fixture（启 lifespan + DB pool）。
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from inalpha_paper.execution.order_executor import OrderExecutor

pytestmark = pytest.mark.integration


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


def test_submit_naked_short_rejected(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """空仓现货 SELL → 409 INSUFFICIENT_POSITION（spot 模式禁裸 SHORT），不落账。

    用独立 symbol(ETH/USDT)确保该账户在此品种上无持仓。
    """
    r = client.post(
        "/orders/submit",
        headers=auth_headers,
        json={
            "symbol": "ETH/USDT",
            "side": "SELL",
            "type": "MARKET",
            "quantity": 0.01,
            "ref_price": 3_000.0,
        },
    )
    assert r.status_code == 409, r.json()
    assert r.json()["code"] == "INSUFFICIENT_POSITION"
    # 不落账：该品种不应出现订单
    listed = client.get("/orders", headers=auth_headers, params={"symbol": "ETH/USDT"})
    assert listed.status_code == 200
    assert listed.json() == []


def test_perp_on_non_crypto_venue_rejected(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """perp 仅 crypto:非 crypto venue 开杠杆 → 422 PERP_NOT_ELIGIBLE。"""
    r = client.post(
        "/orders/submit",
        headers=auth_headers,
        json={"symbol": "AAPL", "venue": "yfinance", "side": "BUY", "type": "MARKET",
              "quantity": 1, "ref_price": 100.0, "trading_mode": "perp", "leverage": 2},
    )
    assert r.status_code == 422, r.json()
    assert r.json()["code"] == "PERP_NOT_ELIGIBLE"


def test_perp_on_spot_symbol_rejected(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """perp 标的须为 USDT-M 永续(ccxt 后缀 :USDT):现货 symbol → 422。"""
    r = client.post(
        "/orders/submit",
        headers=auth_headers,
        json={"symbol": "BTC/USDT", "venue": "binance", "side": "SELL", "type": "MARKET",
              "quantity": 0.01, "ref_price": 50_000.0, "trading_mode": "perp", "leverage": 2},
    )
    assert r.status_code == 422, r.json()
    assert r.json()["code"] == "PERP_NOT_ELIGIBLE"


def test_perp_leverage_out_of_range_rejected(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """杠杆越界(>20)→ 422(pydantic 请求校验)。"""
    r = client.post(
        "/orders/submit",
        headers=auth_headers,
        json={"symbol": "BTC/USDT:USDT", "venue": "binance", "side": "SELL", "type": "MARKET",
              "quantity": 0.01, "ref_price": 50_000.0, "trading_mode": "perp", "leverage": 50},
    )
    assert r.status_code == 400  # pydantic 请求校验失败,本服务统一返 400


def test_perp_margin_gate_allows_cover_when_wallet_eroded(
    client: TestClient, fresh_user: dict[str, str]
) -> None:
    """回归(CR major):perp 平仓单按**成交后目标仓**算 IM(≈0),钱包被亏损侵蚀也不误拒 cover。

    裸 notional 算法把 cover 当等量开仓多算全额 IM → 钱包 < 开仓 IM 时误拒(且回测能过、
    实盘拒,口径分叉)。修复后与回测 Portfolio.can_afford_buy 同口径。
    """
    import asyncio
    from decimal import Decimal

    from inalpha_shared.db import get_conn

    from inalpha_paper.account_id import account_id_from_sub
    from inalpha_paper.storage import accounts as accounts_store

    headers = {"Authorization": fresh_user["Authorization"]}
    account_id = account_id_from_sub(fresh_user["sub"])
    sym = "BTC/USDT:USDT"

    async def _fund(amount: str) -> None:
        async with get_conn() as conn:
            await accounts_store.get_or_create(conn, account_id)
            await accounts_store.apply_cash_delta(
                conn, account_id, Decimal(amount), currency="USDT"
            )

    # 1) 注资够开 0.01 短空(leverage=1 → IM=0.01×50000=500),开空
    asyncio.get_event_loop().run_until_complete(_fund("600"))
    r_open = client.post(
        "/orders/submit", headers=headers,
        json={"symbol": sym, "venue": "binance", "side": "SELL", "type": "MARKET",
              "quantity": 0.01, "ref_price": 50_000.0, "trading_mode": "perp", "leverage": 1},
    )
    assert r_open.status_code == 200, r_open.json()

    # 2) 模拟亏损把钱包侵蚀到 < 开仓 IM(500)
    asyncio.get_event_loop().run_until_complete(_fund("-550"))  # ≈ 49.5 USDT 剩余

    # 3) cover BUY 0.01 平掉短空:目标仓=0 → prospective IM=0,只需 fee → 放行(旧算法会 409)
    r_cover = client.post(
        "/orders/submit", headers=headers,
        json={"symbol": sym, "venue": "binance", "side": "BUY", "type": "MARKET",
              "quantity": 0.01, "ref_price": 50_000.0, "trading_mode": "perp", "leverage": 1},
    )
    assert r_cover.status_code == 200, r_cover.json()
    assert r_cover.json()["status"] == "FILLED"
