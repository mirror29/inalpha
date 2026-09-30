"""交易执行的 fresh ticker 跨服务契约回归测试。"""
from __future__ import annotations

from typing import Any

import pytest
import respx
from fastapi.testclient import TestClient
from httpx import Response

from inalpha_paper.data_client import DataClient
from inalpha_paper.schemas import CreatePlanRequest, SubmitOrderRequest

from .conftest import fresh_account_token

pytestmark = pytest.mark.integration


def _ticker_response(
    *,
    venue: str,
    symbol: str,
    price: float,
    source: str,
    is_stale: bool = False,
    stale_seconds: int = 1,
) -> dict[str, Any]:
    """构造 data-service 的完整 ticker 响应。"""
    return {
        "venue": venue,
        "symbol": symbol,
        "price": price,
        "ts": "2026-08-13T02:20:00Z",
        "source": source,
        "is_stale": is_stale,
        "stale_seconds": stale_seconds,
    }


@pytest.mark.parametrize(
    ("fresh", "expected"),
    [(True, "true"), (False, "false"), (None, None)],
)
@respx.mock
async def test_data_client_serializes_fresh_query(
    fresh: bool | None, expected: str | None
) -> None:
    """DataClient 只在 caller 明确选择时发送 fresh 参数。"""
    route = respx.get("http://data-mock.test/ticker").mock(
        return_value=Response(
            200,
            json=_ticker_response(
                venue="baostock",
                symbol="sh.600519",
                price=1_415.0,
                source="baostock_ticker",
            ),
        )
    )

    async with DataClient("http://data-mock.test", "test-token") as data_client:
        await data_client.get_ticker(
            venue="baostock",
            symbol="sh.600519",
            fresh=fresh,
        )

    assert route.called
    request = route.calls.last.request
    assert request.url.params.get("venue") == "baostock"
    assert request.url.params.get("symbol") == "sh.600519"
    assert request.url.params.get("fresh") == expected


def test_trade_request_schemas_canonicalize_market_identities() -> None:
    """direct order 与 plan 都在持久化前统一 legacy venue/symbol。"""
    order = SubmitOrderRequest.model_validate(
        {
            "venue": "akshare",
            "symbol": "600519.SH",
            "side": "BUY",
            "type": "MARKET",
            "quantity": 1,
        }
    )
    plan = CreatePlanRequest.model_validate(
        {
            "intent": "open_long",
            "venue": "baostock",
            "symbol": "000001.SZ",
            "side": "BUY",
            "type": "MARKET",
            "quantity": 1,
            "rationale": "canonical identity regression",
        }
    )
    legacy_global = SubmitOrderRequest.model_validate(
        {
            "venue": "akshare",
            "symbol": "hk.00700",
            "side": "SELL",
            "type": "MARKET",
            "quantity": 1,
        }
    )

    assert (order.venue, order.symbol) == ("baostock", "sh.600519")
    assert (plan.venue, plan.symbol) == ("baostock", "sz.000001")
    assert (legacy_global.venue, legacy_global.symbol) == ("yfinance", "0700.HK")


def test_submit_sell_migrates_single_legacy_a_share_position(client: TestClient) -> None:
    """升级前旧 key 的持仓仍能被 canonical SELL 找到并平仓。"""
    import asyncio
    from decimal import Decimal

    from inalpha_shared.db import get_conn

    from inalpha_paper.account_id import account_id_from_sub
    from inalpha_paper.storage import accounts as accounts_store

    sub, token = fresh_account_token("legacy-a-share-position")
    account_id = account_id_from_sub(sub)
    headers = {"Authorization": f"Bearer {token}"}

    async def _seed_legacy_position() -> None:
        async with get_conn() as conn:
            await accounts_store.get_or_create(conn, account_id)
            async with conn.cursor() as cur:
                await cur.execute(
                    "INSERT INTO positions ("
                    "account_id, venue, symbol, quantity, avg_open_price, "
                    "realized_pnl, generation, currency, updated_at"
                    ") VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW())",
                    (
                        str(account_id),
                        "akshare",
                        "600519.SH",
                        Decimal("1"),
                        Decimal("1415"),
                        Decimal("0"),
                        1,
                        "USD",
                    ),
                )

    asyncio.run(_seed_legacy_position())

    response = client.post(
        "/orders/submit",
        headers=headers,
        json={
            "venue": "baostock",
            "symbol": "sh.600519",
            "side": "SELL",
            "type": "MARKET",
            "quantity": 1,
            "ref_price": 1_415.0,
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "FILLED"

    async def _read_currency() -> str:
        async with get_conn() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT currency FROM positions WHERE account_id = %s",
                    (str(account_id),),
                )
                row = await cur.fetchone()
                assert row is not None
                return str(row["currency"])

    assert asyncio.run(_read_currency()) == "CNY"
    assert client.get("/positions", headers=headers).json() == []


@pytest.mark.parametrize("legacy_venue", ["akshare", "baostock"])
@respx.mock
def test_submit_sell_migrates_legacy_global_position_with_fresh_ticker(
    client: TestClient,
    legacy_venue: str,
) -> None:
    """旧前缀格式港股持仓可用 canonical fresh ticker 完成市价平仓。"""
    import asyncio
    from decimal import Decimal

    from inalpha_shared.db import get_conn

    from inalpha_paper.account_id import account_id_from_sub
    from inalpha_paper.storage import accounts as accounts_store

    sub, token = fresh_account_token(f"legacy-global-position-{legacy_venue}")
    account_id = account_id_from_sub(sub)
    headers = {"Authorization": f"Bearer {token}"}

    async def _seed_legacy_position() -> None:
        async with get_conn() as conn:
            await accounts_store.get_or_create(conn, account_id)
            async with conn.cursor() as cur:
                await cur.execute(
                    "INSERT INTO positions ("
                    "account_id, venue, symbol, quantity, avg_open_price, "
                    "realized_pnl, generation, currency, updated_at"
                    ") VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW())",
                    (
                        str(account_id),
                        legacy_venue,
                        "hk.00700",
                        Decimal("1"),
                        Decimal("600"),
                        Decimal("0"),
                        1,
                        "USD",
                    ),
                )

    asyncio.run(_seed_legacy_position())
    visible_rows = client.get("/positions", headers=headers).json()
    assert len(visible_rows) == 1
    assert (visible_rows[0]["venue"], visible_rows[0]["symbol"]) == (
        "yfinance",
        "0700.HK",
    )
    assert visible_rows[0]["currency"] == "HKD"
    route = respx.get(
        "http://data-mock.test/ticker",
        params={"venue": "yfinance", "symbol": "0700.HK", "fresh": "true"},
    ).mock(
        return_value=Response(
            200,
            json=_ticker_response(
                venue="yfinance",
                symbol="0700.HK",
                price=620.0,
                source="yfinance_ticker",
            ),
        )
    )

    response = client.post(
        "/orders/submit",
        headers=headers,
        json={
            "venue": legacy_venue,
            "symbol": "hk.00700",
            "side": "SELL",
            "type": "MARKET",
            "quantity": 1,
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "FILLED"
    assert (body["venue"], body["symbol"]) == ("yfinance", "0700.HK")
    assert body["avg_fill_price"] == 620.0
    assert route.call_count == 1

    async def _read_raw_position() -> dict[str, Any]:
        async with get_conn() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT venue, symbol, currency FROM positions WHERE account_id = %s",
                    (str(account_id),),
                )
                row = await cur.fetchone()
                assert row is not None
                return dict(row)

    stored = asyncio.run(_read_raw_position())
    assert (stored["venue"], stored["symbol"]) == ("yfinance", "0700.HK")
    assert stored["currency"] == "HKD"
    assert client.get("/positions", headers=headers).json() == []


def test_account_snapshot_canonicalizes_legacy_global_position_for_mark(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """账户估值读取旧全球仓位时使用 canonical ticker 与正确计价币种。"""
    import asyncio
    from decimal import Decimal

    from inalpha_shared.db import get_conn

    from inalpha_paper.account_id import account_id_from_sub
    from inalpha_paper.storage import accounts as accounts_store

    ticker_calls: list[tuple[str, str, bool | None]] = []
    fx_calls: list[tuple[str, str]] = []

    class _StubDataClient:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        async def get_ticker(
            self,
            *,
            venue: str,
            symbol: str,
            fresh: bool | None = None,
        ) -> dict[str, Any]:
            ticker_calls.append((venue, symbol, fresh))
            return {"price": 620.0, "is_stale": False}

        async def get_fx(self, *, base: str, quote: str) -> dict[str, Any]:
            fx_calls.append((base, quote))
            return {"rate": 0.1, "is_stale": False}

        async def close(self) -> None:
            pass

    monkeypatch.setattr("inalpha_paper.api.orders.DataClient", _StubDataClient)
    sub, token = fresh_account_token("legacy-global-account-snapshot")
    account_id = account_id_from_sub(sub)
    headers = {"Authorization": f"Bearer {token}"}

    async def _seed_legacy_position() -> None:
        async with get_conn() as conn:
            await accounts_store.get_or_create(conn, account_id)
            async with conn.cursor() as cur:
                await cur.execute(
                    "INSERT INTO positions ("
                    "account_id, venue, symbol, quantity, avg_open_price, "
                    "realized_pnl, generation, currency, updated_at"
                    ") VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW())",
                    (
                        str(account_id),
                        "baostock",
                        "hk.00700",
                        Decimal("1"),
                        Decimal("600"),
                        Decimal("0"),
                        1,
                        "USD",
                    ),
                )

    asyncio.run(_seed_legacy_position())
    response = client.get("/accounts/me", headers=headers)

    assert response.status_code == 200, response.text
    body = response.json()
    assert ticker_calls == [("yfinance", "0700.HK", False)]
    assert fx_calls == [("HKD", "USD")]
    assert body["positions_value"] == pytest.approx(62.0)
    assert body["fx_warnings"] == []


@respx.mock
def test_submit_order_without_ref_price_requests_fresh_ticker(
    client: TestClient,
) -> None:
    """直接下单的自动参考价必须来自 fresh ticker，而不是 DB bar 缓存。"""
    _, token = fresh_account_token("fresh-order")
    route = respx.get(
        "http://data-mock.test/ticker",
        params={"venue": "binance", "symbol": "BTC/USDT", "fresh": "true"},
    ).mock(
        return_value=Response(
            200,
            json=_ticker_response(
                venue="binance",
                symbol="BTC/USDT",
                price=50_000.0,
                source="binance_ticker",
            ),
        )
    )

    response = client.post(
        "/orders/submit",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "venue": "binance",
            "symbol": "BTC/USDT",
            "side": "BUY",
            "type": "MARKET",
            "quantity": 0.01,
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "FILLED"
    assert body["avg_fill_price"] == 50_000.0
    assert route.call_count == 1


@respx.mock
def test_submit_order_rejects_stale_execution_ticker(client: TestClient) -> None:
    """交易执行不得把 data 明确标记的陈旧报价当作成交价。"""
    _, token = fresh_account_token("stale-order")
    route = respx.get(
        "http://data-mock.test/ticker",
        params={"venue": "binance", "symbol": "BTC/USDT", "fresh": "true"},
    ).mock(
        return_value=Response(
            200,
            json=_ticker_response(
                venue="binance",
                symbol="BTC/USDT",
                price=50_000.0,
                source="binance_ticker",
                is_stale=True,
                stale_seconds=900,
            ),
        )
    )

    response = client.post(
        "/orders/submit",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "venue": "binance",
            "symbol": "BTC/USDT",
            "side": "BUY",
            "type": "MARKET",
            "quantity": 0.01,
        },
    )

    assert response.status_code == 400, response.text
    assert response.json()["code"] == "REF_PRICE_UNAVAILABLE"
    assert route.call_count == 1


@pytest.mark.parametrize(
    "first_response",
    [
        pytest.param(
            Response(
                404,
                json={
                    "code": "NO_PRICE_AVAILABLE",
                    "message": "no cached or live price available",
                },
            ),
            id="unavailable",
        ),
        pytest.param(
            Response(
                200,
                json=_ticker_response(
                    venue="baostock",
                    symbol="sh.600519",
                    price=1_415.0,
                    source="baostock_ticker",
                    is_stale=True,
                    stale_seconds=900,
                ),
            ),
            id="stale",
        ),
    ],
)
@respx.mock
def test_a_share_plan_retries_same_token_after_fresh_quote_recovers(
    client: TestClient,
    first_response: Response,
) -> None:
    """A 股报价失败不消费 token，恢复后同一 plan 可按 fresh ticker 成交。"""
    import asyncio

    from inalpha_shared.db import get_conn

    respx.get("http://data-mock.test/fx", params={"base": "CNY", "quote": "USD"}).mock(
        return_value=Response(200, json={"rate": 0.14, "is_stale": False})
    )
    _, token = fresh_account_token("fresh-a-share-plan")
    headers = {"Authorization": f"Bearer {token}"}
    route = respx.get(
        "http://data-mock.test/ticker",
        params={"venue": "baostock", "symbol": "sh.600519", "fresh": "true"},
    ).mock(
        side_effect=[
            first_response,
            Response(
                200,
                json=_ticker_response(
                    venue="baostock",
                    symbol="sh.600519",
                    price=1_415.0,
                    source="baostock_ticker",
                ),
            ),
        ]
    )

    created = client.post(
        "/plans",
        headers=headers,
        json={
            "intent": "open_long",
            "venue": "baostock",
            "symbol": "600519.SH",
            "side": "BUY",
            "type": "MARKET",
            "quantity": 1,
            "rationale": "verify A-share fresh quote execution",
            "expire_in_seconds": 300,
        },
    )
    assert created.status_code == 200, created.text
    created_body = created.json()
    assert (created_body["venue"], created_body["symbol"]) == (
        "baostock",
        "sh.600519",
    )
    plan_id = created_body["plan_id"]

    async def _rewrite_as_legacy_plan() -> None:
        async with get_conn() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "UPDATE trade_plans SET venue = %s, symbol = %s WHERE plan_id = %s",
                    ("akshare", "600519.SH", plan_id),
                )

    asyncio.run(_rewrite_as_legacy_plan())

    approved = client.post(
        f"/plans/{plan_id}/approve",
        headers=headers,
        json={"approver": "tester"},
    )
    assert approved.status_code == 200, approved.text
    approval_token = approved.json()["approval_token"]

    first = client.post(
        f"/plans/{plan_id}/execute",
        headers=headers,
        json={"approvalToken": approval_token},
    )
    assert first.status_code == 400, first.text
    assert first.json()["code"] == "REF_PRICE_UNAVAILABLE"

    after_failure = client.get(f"/plans/{plan_id}", headers=headers)
    assert after_failure.status_code == 200, after_failure.text
    assert after_failure.json()["status"] == "approved"
    assert after_failure.json()["resulting_order_id"] is None

    second = client.post(
        f"/plans/{plan_id}/execute",
        headers=headers,
        json={"approvalToken": approval_token},
    )
    assert second.status_code == 200, second.text
    result = second.json()
    assert result["plan_status"] == "executed"
    assert result["order"]["status"] == "FILLED"
    assert result["order"]["symbol"] == "sh.600519"
    assert result["order"]["avg_fill_price"] == 1_415.0

    opened_positions = client.get("/positions", headers=headers)
    assert opened_positions.status_code == 200, opened_positions.text
    opened_rows = opened_positions.json()
    assert len(opened_rows) == 1
    assert opened_rows[0]["venue"] == "baostock"
    assert opened_rows[0]["symbol"] == "sh.600519"
    assert opened_rows[0]["currency"] == "CNY"
    assert opened_rows[0]["quantity"] == 1.0

    closed = client.post(
        "/orders/submit",
        headers=headers,
        json={
            "venue": "baostock",
            "symbol": "sh.600519",
            "side": "SELL",
            "type": "MARKET",
            "quantity": 1,
            "ref_price": 1_415.0,
        },
    )
    assert closed.status_code == 200, closed.text
    assert closed.json()["status"] == "FILLED"

    positions = client.get("/positions", headers=headers)
    assert positions.status_code == 200, positions.text
    rows = positions.json()
    assert rows == []
    assert route.call_count == 2
