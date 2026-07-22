"""Tests for GET /news endpoint — extended to support baostock venue."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.anyio


def test_news_requires_auth(client: TestClient) -> None:
    """GET /news without token returns 401."""
    r = client.get("/news", params={"venue": "yfinance", "symbol": "AAPL"})
    assert r.status_code == 401


def test_news_yfinance_venue(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """GET /news with venue=yfinance returns results."""
    from inalpha_data.connectors import yfinance_conn as yf

    original = yf._connector.fetch_news

    async def mock_news(symbol, limit=20):
        return [
            {
                "title": "Test",
                "publisher": "Reuters",
                "link": "https://x.com",
                "published_at": "2026-05-29T00:00:00+00:00",
                "summary": "test",
            }
        ]

    yf._connector.fetch_news = mock_news
    try:
        r = client.get(
            "/news",
            headers=auth_headers,
            params={"venue": "yfinance", "symbol": "AAPL"},
        )
        assert r.status_code == 200
        body = r.json()
        assert len(body["items"]) == 1
        assert body["items"][0]["title"] == "Test"
    finally:
        yf._connector.fetch_news = original


def test_news_baostock_venue(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """GET /news with venue=baostock returns A-share news."""
    from inalpha_data.connectors import baostock as bs

    original = bs._connector.fetch_news

    async def mock_news(symbol, limit=20):
        return [
            {
                "title": "茅台发布2026年Q1财报",
                "publisher": "东方财富",
                "link": "https://example.com",
                "published_at": "2026-05-29T10:30:00+00:00",
                "summary": "贵州茅台发布Q1报告...",
            }
        ]

    bs._connector.fetch_news = mock_news
    try:
        r = client.get(
            "/news",
            headers=auth_headers,
            params={"venue": "baostock", "symbol": "sh.600519"},
        )
        assert r.status_code == 200
        body = r.json()
        assert len(body["items"]) == 1
        assert "茅台" in body["items"][0]["title"]
    finally:
        bs._connector.fetch_news = original


def test_news_unsupported_venue(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """Venue=binance should return 422."""
    r = client.get(
        "/news",
        headers=auth_headers,
        params={"venue": "binance", "symbol": "BTC/USDT"},
    )
    assert r.status_code == 400
    assert "NEWS" in r.json()["code"]
