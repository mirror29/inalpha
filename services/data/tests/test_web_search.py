"""Tests for GET /web/search and GET /web/news endpoints (ddgs connector)."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.anyio


def test_web_search_requires_auth(client: TestClient) -> None:
    """GET /web/search without token returns 401."""
    r = client.get("/web/search", params={"query": "test"})
    assert r.status_code == 401


def test_web_news_requires_auth(client: TestClient) -> None:
    """GET /web/news without token returns 401."""
    r = client.get("/web/news", params={"query": "test"})
    assert r.status_code == 401


def test_web_search_mock_returns_results(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """Mocked ddgs returns fake results → endpoint returns WebSearchResponse."""
    from inalpha_data.connectors import web_search as ws

    original = ws._connector.fetch_search

    async def mock_fetch(query, backend="auto", max_results=10):
        return [
            {"title": "Test Result", "href": "https://example.com", "body": "A test snippet"}
        ]

    ws._connector.fetch_search = mock_fetch
    try:
        r = client.get(
            "/web/search",
            headers=auth_headers,
            params={"query": "Bitcoin", "max_results": 5},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["query"] == "Bitcoin"
        assert len(body["results"]) == 1
        assert body["results"][0]["title"] == "Test Result"
        assert body["results"][0]["url"] == "https://example.com"
    finally:
        ws._connector.fetch_search = original


def test_web_news_mock_returns_results(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """Mocked ddgs news returns fake results → endpoint returns WebSearchResponse."""
    from inalpha_data.connectors import web_search as ws

    original = ws._connector.fetch_news

    async def mock_news(query, max_results=10):
        return [
            {
                "title": "News Headline",
                "href": "https://news.example.com/article",
                "body": "A news snippet about the topic.",
            }
        ]

    ws._connector.fetch_news = mock_news
    try:
        r = client.get(
            "/web/news",
            headers=auth_headers,
            params={"query": "Crypto", "max_results": 3},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["query"] == "Crypto"
        assert body["backend"] == "news"
        assert len(body["results"]) == 1
        assert body["results"][0]["title"] == "News Headline"
        assert body["results"][0]["url"] == "https://news.example.com/article"
    finally:
        ws._connector.fetch_news = original


def test_web_search_respects_max_results(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """max_results > 20 should be rejected with 422 validation error."""
    r = client.get(
        "/web/search",
        headers=auth_headers,
        params={"query": "test", "max_results": 25},
    )
    assert r.status_code == 400


def test_web_search_max_results_min_bound(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """max_results < 1 should be rejected with 422 validation error."""
    r = client.get(
        "/web/search",
        headers=auth_headers,
        params={"query": "test", "max_results": 0},
    )
    assert r.status_code == 400


async def test_run_guarded_swallows_non_timeout_exceptions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_run_guarded 对非 TimeoutError 异常也兜底返回 []，不穿透到上层 analyst fan-out。

    修复前只 except TimeoutError，底层（DDGS / to_thread）抛的其它异常会直接传出，
    让整条 analyst 链崩掉。此 test 在修复前会因 RuntimeError 未捕获而失败。
    """
    from inalpha_data.connectors import web_search as ws

    def boom(*args: object, **kwargs: object) -> list[dict[str, object]]:
        raise RuntimeError("ddgs blew up")

    monkeypatch.setattr(ws, "_search_sync", boom)
    connector = ws.WebSearchConnector()
    result = await connector.fetch_search("anything", max_results=3)
    assert result == []
