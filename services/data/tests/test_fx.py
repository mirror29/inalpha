"""Tests for GET /fx endpoint（D-11）。"""
from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.anyio


def test_fx_requires_auth(client: TestClient) -> None:
    r = client.get("/fx", params={"base": "CNY", "quote": "USD"})
    assert r.status_code == 401


def test_fx_identity(client: TestClient, auth_headers: dict[str, str]) -> None:
    """base == quote → 1.0，source=identity，无网络。"""
    r = client.get("/fx", headers=auth_headers, params={"base": "USD", "quote": "USD"})
    assert r.status_code == 200
    body = r.json()
    assert body["rate"] == 1.0
    assert body["source"] == "identity"
    assert body["is_stale"] is False


def test_fx_stablecoin(client: TestClient, auth_headers: dict[str, str]) -> None:
    """USDT/USD 等 USD 稳定币 → 1.0，source=stablecoin。"""
    r = client.get("/fx", headers=auth_headers, params={"base": "USDT", "quote": "USD"})
    assert r.status_code == 200
    body = r.json()
    assert body["rate"] == 1.0
    assert body["source"] == "stablecoin"


def test_fx_yfinance_success(client: TestClient, auth_headers: dict[str, str]) -> None:
    """真实货币对走 yfinance forex pair。"""
    from inalpha_data.connectors import yfinance_conn as yf

    original = yf._connector.fetch_ticker
    captured: dict[str, str] = {}

    async def mock_ticker(symbol: str):
        captured["symbol"] = symbol
        return datetime.now(UTC), 0.14

    yf._connector.fetch_ticker = mock_ticker
    try:
        r = client.get("/fx", headers=auth_headers, params={"base": "CNY", "quote": "USD"})
        assert r.status_code == 200
        body = r.json()
        assert body["rate"] == 0.14
        assert body["source"] == "yfinance"
        assert captured["symbol"] == "CNYUSD=X"  # forex pair 形态
    finally:
        yf._connector.fetch_ticker = original


def test_fx_yfinance_failure_returns_502(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """yfinance 拿不到 → 502 FX_UNAVAILABLE（不静态兜底真实汇率）。"""
    from inalpha_data.connectors import yfinance_conn as yf

    original = yf._connector.fetch_ticker

    async def mock_ticker_fail(symbol: str):
        raise ValueError("no last_price")

    yf._connector.fetch_ticker = mock_ticker_fail
    try:
        r = client.get("/fx", headers=auth_headers, params={"base": "CNY", "quote": "USD"})
        assert r.status_code == 502
        assert r.json()["code"] == "FX_UNAVAILABLE"
    finally:
        yf._connector.fetch_ticker = original
