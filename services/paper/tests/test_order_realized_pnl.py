"""集成测试：每笔订单的已实现盈亏(orders.realized_pnl)。

口径(与 position.realized_pnl / closed_trades.close_profit_abs 一致):
- 开仓/加仓单 → realized_pnl == 0
- 平/减仓单 → realized_pnl == (卖出价 - 买入均价) * 平掉数量(毛口径,不减手续费)

经 HTTP /orders/submit 下单,再用 GET /orders 读回 realized_pnl 校验。
"""
from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from inalpha_shared.db import get_conn

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
async def _truncate(app_with_lifespan):  # type: ignore[no-untyped-def]
    async with get_conn() as conn:
        async with conn.cursor() as cur:
            await cur.execute("TRUNCATE TABLE closed_trades RESTART IDENTITY")
            await cur.execute("TRUNCATE TABLE positions RESTART IDENTITY CASCADE")
            await cur.execute("TRUNCATE TABLE orders RESTART IDENTITY CASCADE")
            await cur.execute("TRUNCATE TABLE accounts RESTART IDENTITY CASCADE")
    yield


def _submit(
    client: TestClient,
    auth: dict[str, str],
    *,
    side: str,
    quantity: float,
    ref_price: float,
) -> dict[str, Any]:
    resp = client.post(
        "/orders/submit",
        json={
            "venue": "binance",
            "symbol": "BTC/USDT",
            "side": side,
            "type": "MARKET",
            "quantity": quantity,
            "ref_price": ref_price,
            "fee_rate": 0.0,
        },
        headers={"Authorization": auth["Authorization"]},
    )
    assert resp.status_code == 200, f"unexpected {resp.status_code}: {resp.json()}"
    return resp.json()


def _orders_by_id(client: TestClient, auth: dict[str, str]) -> dict[str, dict[str, Any]]:
    resp = client.get("/orders", headers={"Authorization": auth["Authorization"]})
    assert resp.status_code == 200, f"unexpected {resp.status_code}: {resp.json()}"
    return {o["client_order_id"]: o for o in resp.json()}


class TestOrderRealizedPnl:
    """开仓单盈亏=0,平仓单记该笔实现盈亏。"""

    def test_opening_order_realized_pnl_is_zero(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        buy = _submit(client, auth_headers, side="BUY", quantity=0.02, ref_price=50000.0)
        assert buy["status"] == "FILLED", f"BUY not filled: {buy}"
        orders = _orders_by_id(client, auth_headers)
        assert orders[buy["client_order_id"]]["realized_pnl"] == 0.0

    def test_closing_order_records_realized_pnl(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        buy = _submit(client, auth_headers, side="BUY", quantity=0.02, ref_price=50000.0)
        sell = _submit(client, auth_headers, side="SELL", quantity=0.02, ref_price=60000.0)
        assert sell["status"] == "FILLED", f"SELL not filled: {sell}"

        orders = _orders_by_id(client, auth_headers)
        buy_fill = orders[buy["client_order_id"]]["avg_fill_price"]
        sell_fill = orders[sell["client_order_id"]]["avg_fill_price"]
        # 毛口径:(卖出价 - 买入均价) * 平掉数量
        expected = (sell_fill - buy_fill) * 0.02

        got = orders[sell["client_order_id"]]["realized_pnl"]
        assert got is not None
        assert got == pytest.approx(expected, abs=1e-6), (
            f"realized_pnl {got} != expected {expected}"
        )
        # 开仓单仍为 0,不受平仓影响
        assert orders[buy["client_order_id"]]["realized_pnl"] == 0.0
