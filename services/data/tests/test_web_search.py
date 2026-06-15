"""Tests for GET /web/search and GET /web/news endpoints (ddgs connector)."""
from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from inalpha_data.connectors.web_search import (
    SearchOutcome,
    WebSearchConnector,
    _classify_exception,
)

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
        return SearchOutcome(
            results=[
                {"title": "Test Result", "href": "https://example.com", "body": "A test snippet"}
            ],
            backend_used="bing",
        )

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
        assert body["status"] == "ok"
        assert body["backend"] == "bing"  # 实际引擎透传
        assert body["fetched_at"] is not None
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
        return SearchOutcome(
            results=[
                {
                    "title": "News Headline",
                    "href": "https://news.example.com/article",
                    "body": "A news snippet about the topic.",
                }
            ],
            backend_used="news",
        )

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
        assert body["status"] == "ok"
        assert len(body["results"]) == 1
        assert body["results"][0]["title"] == "News Headline"
        assert body["results"][0]["url"] == "https://news.example.com/article"
    finally:
        ws._connector.fetch_news = original


def test_web_search_endpoint_surfaces_connector_failure(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """connector 抛异常 → 端点 200 + status=engine_error（不再静默返裸空数组）。"""
    from inalpha_data.connectors import web_search as ws

    original = ws._connector.fetch_search

    async def mock_boom(query, backend="auto", max_results=10):
        raise RuntimeError("connector exploded")

    ws._connector.fetch_search = mock_boom
    try:
        r = client.get(
            "/web/search", headers=auth_headers, params={"query": "anything"}
        )
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "engine_error"
        assert "connector exploded" in body["error"]
        assert body["results"] == []
    finally:
        ws._connector.fetch_search = original


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


async def test_run_guarded_returns_engine_error_on_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """非超时异常不上抛（不能穿透 analyst fan-out），但 status 必须标 engine_error。"""
    from inalpha_data.connectors import web_search as ws

    def boom(*args: object, **kwargs: object) -> list[dict[str, object]]:
        raise RuntimeError("ddgs blew up")

    monkeypatch.setattr(ws, "_search_sync", boom)
    connector = ws.WebSearchConnector()
    out = await connector.fetch_search("anything", max_results=3)
    assert out.results == []
    assert out.status == "engine_error"
    assert "ddgs blew up" in (out.error or "")


def test_classify_exception_direct() -> None:
    """直接覆盖 _classify_exception 的类名/消息分类。

    回归卡口：分类靠异常类名字符串匹配（不硬 import ddgs.exceptions）。ddgs 升级若
    重命名异常类（如 RatelimitException→RateLimitException）会让此测试挂，提醒同步。
    间接路径（_run_guarded）mock 的是 outcome，不经真实异常对象，覆盖不到这里。
    """

    class RatelimitException(Exception): ...

    class TimeoutException(Exception): ...

    class DDGSException(Exception): ...

    assert _classify_exception(RatelimitException("429")) == "rate_limited"
    assert _classify_exception(TimeoutException("timed out")) == "timeout"
    # 空结果是抛 DDGSException("No results found.")，按消息识别（优先于类名）
    assert _classify_exception(DDGSException("No results found.")) == "no_results"
    assert _classify_exception(DDGSException("backend exploded")) == "engine_error"
    assert _classify_exception(ValueError("boom")) == "engine_error"


async def test_run_guarded_classifies_rate_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ddgs 429 类异常 → status=rate_limited（按异常类型名识别）。"""
    from inalpha_data.connectors import web_search as ws

    class RatelimitException(Exception):
        pass

    def limited(*args: object, **kwargs: object) -> list[dict[str, object]]:
        raise RatelimitException("429 too many requests")

    monkeypatch.setattr(ws, "_search_sync", limited)
    connector = ws.WebSearchConnector()
    out = await connector.fetch_search("anything", backend="google", max_results=3)
    assert out.status == "rate_limited"


async def test_run_guarded_classifies_no_results_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ddgs 空结果是抛 'No results found.' 异常——必须识别为 no_results 而非引擎故障。"""
    from inalpha_data.connectors import web_search as ws

    def empty(*args: object, **kwargs: object) -> list[dict[str, object]]:
        raise Exception("No results found.")

    monkeypatch.setattr(ws, "_search_sync", empty)
    connector = ws.WebSearchConnector()
    out = await connector.fetch_search("nonexistent gibberish", backend="google", max_results=3)
    assert out.status == "no_results"


async def test_run_guarded_timeout_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """整体超时 → status=timeout（修复前与"无结果"无法区分）。"""
    from inalpha_data.connectors import web_search as ws

    def slow(*args: object, **kwargs: object) -> list[dict[str, object]]:
        time.sleep(0.5)
        return [{"title": "too late", "href": "", "body": ""}]

    monkeypatch.setattr(ws, "_search_sync", slow)
    connector = ws.WebSearchConnector()
    connector._overall_timeout = 0.05
    out = await connector.fetch_search("anything", backend="google", max_results=3)
    assert out.status == "timeout"
    assert out.results == []


async def test_cjk_news_query_downgrades_to_text_search(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """中文 news query 跳过 ddgs.news（实测必空），直接走 text 搜索并带 hint。"""
    from inalpha_data.connectors import web_search as ws

    def news_must_not_be_called(*args: object, **kwargs: object) -> list[dict[str, object]]:
        raise AssertionError("_news_sync should be skipped for CJK queries")

    def text_ok(*args: object, **kwargs: object) -> list[dict[str, object]]:
        return [{"title": "中文新闻", "href": "https://example.cn", "body": "正文"}]

    monkeypatch.setattr(ws, "_news_sync", news_must_not_be_called)
    monkeypatch.setattr(ws, "_search_sync", text_ok)
    connector = WebSearchConnector()
    out = await connector.fetch_news("某市场 大涨 原因", max_results=5)
    assert out.status == "ok"
    assert out.results[0]["title"] == "中文新闻"
    assert "text-fallback-for-cjk-news" in out.backend_used
    assert out.hint is not None and "data.get_market_news" in out.hint
