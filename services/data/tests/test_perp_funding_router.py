"""``GET /perp/funding`` 端点测试(mock binance connector,零网络)。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.integration


def test_perp_funding_returns_mark_and_rate(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    r = client.get(
        "/perp/funding",
        params={"venue": "binance", "symbol": "BTC/USDT:USDT"},
        headers=auth_headers,
    )
    assert r.status_code == 200, r.json()
    b = r.json()
    assert b["symbol"] == "BTC/USDT:USDT"
    assert b["mark_price"] == 60000.0
    assert b["funding_rate"] == 0.0001
    assert b["next_funding_ts"] is not None


def test_perp_funding_unregistered_venue_422(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    r = client.get(
        "/perp/funding",
        params={"venue": "nope", "symbol": "X/USDT:USDT"},
        headers=auth_headers,
    )
    assert r.status_code == 422
    assert r.json()["code"] == "PERP_NOT_SUPPORTED_FOR_VENUE"


def test_perp_funding_requires_auth(client: TestClient) -> None:
    r = client.get("/perp/funding", params={"venue": "binance", "symbol": "BTC/USDT:USDT"})
    assert r.status_code == 401


def test_funding_history_rejects_naive_or_overlong_ranges(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    for start, end in [
        ("2026-09-01T00:00:00", "2026-09-02T00:00:00"),
        ("2026-09-01T00:00:00Z", "2026-09-10T00:00:00Z"),
    ]:
        response = client.get(
            "/perp/funding/history",
            headers=auth_headers,
            params={"venue": "binance", "symbol": "BTC/USDT:USDT", "from_ts": start, "to_ts": end},
        )
        assert response.status_code == 422


def test_funding_history_requires_owner_jwt(client: TestClient) -> None:
    assert (
        client.get(
            "/perp/funding/history",
            params={
                "venue": "binance",
                "symbol": "BTC/USDT:USDT",
                "from_ts": "2026-09-01T00:00:00Z",
                "to_ts": "2026-09-02T00:00:00Z",
            },
        ).status_code
        == 401
    )
