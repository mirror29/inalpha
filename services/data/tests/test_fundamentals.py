"""Tests for GET /fundamentals endpoint."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.anyio


def test_fundamentals_requires_auth(client: TestClient) -> None:
    """GET /fundamentals without token returns 401."""
    r = client.get("/fundamentals", params={"venue": "akshare", "symbol": "sh.600519"})
    assert r.status_code == 401


def test_fundamentals_akshare_venue(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """Mocked akshare connector returns financial data."""
    from inalpha_data.connectors import akshare as ak

    original = ak._connector.fetch_financials

    async def mock_fin(symbol):
        return {
            "venue": "akshare",
            "symbol": symbol,
            "available": True,
            "as_of": "2026-05-29T00:00:00Z",
            "indicators": {
                "market_cap": 2.3e12,
                "pe_ratio": 32.5,
                "roe": 0.283,
                "revenue_yoy": 0.153,
            },
            "raw": {"总市值": "2300000000000"},
        }

    ak._connector.fetch_financials = mock_fin
    try:
        r = client.get(
            "/fundamentals",
            headers=auth_headers,
            params={"venue": "akshare", "symbol": "sh.600519"},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["available"] is True
        assert body["indicators"]["pe_ratio"] == 32.5
        assert body["indicators"]["roe"] == 0.283
    finally:
        ak._connector.fetch_financials = original


def test_fundamentals_yfinance_venue(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """GET /fundamentals with venue=yfinance works."""
    from inalpha_data.connectors import yfinance_conn as yf

    original = yf._connector.fetch_financials

    async def mock_fin(symbol):
        return {
            "venue": "yfinance",
            "symbol": symbol,
            "available": True,
            "as_of": "2026-05-29T00:00:00Z",
            "indicators": {"market_cap": 3.5e12, "pe_ratio": 28.0, "roe": 1.45},
            "raw": {},
        }

    yf._connector.fetch_financials = mock_fin
    try:
        r = client.get(
            "/fundamentals",
            headers=auth_headers,
            params={"venue": "yfinance", "symbol": "AAPL"},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["available"] is True
        assert body["indicators"]["pe_ratio"] == 28.0
    finally:
        yf._connector.fetch_financials = original


def test_fundamentals_unsupported_venue(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """Venue=binance should return 422."""
    r = client.get(
        "/fundamentals",
        headers=auth_headers,
        params={"venue": "binance", "symbol": "BTC/USDT"},
    )
    assert r.status_code == 400
    assert "FUNDAMENTALS" in r.json()["code"]


def test_fundamentals_unavailable_data(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """Connector returns available=False → endpoint returns 200 with available=False."""
    from inalpha_data.connectors import akshare as ak

    original = ak._connector.fetch_financials

    async def mock_fin(symbol):
        return {
            "venue": "akshare",
            "symbol": symbol,
            "available": False,
            "reason": "no data",
        }

    ak._connector.fetch_financials = mock_fin
    try:
        r = client.get(
            "/fundamentals",
            headers=auth_headers,
            params={"venue": "akshare", "symbol": "jp.6758"},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["available"] is False
    finally:
        ak._connector.fetch_financials = original
