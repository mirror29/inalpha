"""web_fetch connector + GET /web/fetch 端点测试（不打外网）。"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from inalpha_data.connectors.web_fetch import (
    WebFetchConnector,
    _extract_sync,
    _is_private_host,
)

pytestmark = pytest.mark.anyio


# ────────────────────────────────────────────────────────────────────
# SSRF / 协议护栏
# ────────────────────────────────────────────────────────────────────


async def test_rejects_non_http_scheme() -> None:
    conn = WebFetchConnector()
    out = await conn.fetch_page("ftp://example.com/file")
    assert "error" in out and "http" in out["error"]


async def test_rejects_private_hosts() -> None:
    conn = WebFetchConnector()
    for url in (
        "http://127.0.0.1:8002/internal",
        "http://localhost/x",
        "http://192.168.1.1/admin",
        "http://10.0.0.5/",
    ):
        out = await conn.fetch_page(url)
        assert "error" in out, url
        assert "private" in out["error"] or "loopback" in out["error"], url


def test_is_private_host_classification() -> None:
    assert _is_private_host("127.0.0.1") is True
    assert _is_private_host("localhost") is True
    assert _is_private_host("10.1.2.3") is True
    assert _is_private_host("169.254.0.1") is True
    # 公网 IP 字面量不应误判（不查 DNS）
    assert _is_private_host("1.1.1.1") is False


async def test_rejects_missing_host() -> None:
    conn = WebFetchConnector()
    out = await conn.fetch_page("https:///path-only")
    assert "error" in out


# ────────────────────────────────────────────────────────────────────
# 正文抽取（trafilatura → bs4 fallback）
# ────────────────────────────────────────────────────────────────────


def test_extract_sync_article_html() -> None:
    html = (
        "<html><head><title>测试公告标题</title></head><body>"
        "<nav>导航噪音</nav>"
        "<article><p>" + "公司一季度经营现金流净额为十亿元。" * 20 + "</p></article>"
        "</body></html>"
    )
    out = _extract_sync(html, "https://example.com/announcement")
    assert "经营现金流" in out["text"]
    assert out["title"] and "测试公告" in out["title"]


def test_extract_sync_falls_back_on_garbage() -> None:
    # 完全非 HTML 的输入：trafilatura 抽不出 → bs4 fallback 也安全返回
    out = _extract_sync("just some plain text, no markup", "https://example.com/x")
    assert isinstance(out["text"], str)


# ────────────────────────────────────────────────────────────────────
# 截断与端点
# ────────────────────────────────────────────────────────────────────


async def test_fetch_page_truncates_to_max_chars(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = WebFetchConnector()

    async def fake_fetch_and_extract(url: str, max_chars: int) -> dict:
        text = "x" * 1000
        return {
            "url": url,
            "final_url": url,
            "title": "t",
            "published_at": None,
            "text": text[:max_chars],
            "truncated": len(text) > max_chars,
            "fetched_at": "2026-06-11T00:00:00Z",
        }

    monkeypatch.setattr(conn, "_fetch_and_extract", fake_fetch_and_extract)
    out = await conn.fetch_page("https://example.com/long", max_chars=100)
    assert out["truncated"] is True
    assert len(out["text"]) == 100


def test_web_fetch_requires_auth(client: TestClient) -> None:
    r = client.get("/web/fetch", params={"url": "https://example.com"})
    assert r.status_code == 401


def test_web_fetch_endpoint_shape(client: TestClient, auth_headers: dict[str, str]) -> None:
    from inalpha_data.connectors import web_fetch as wf

    original = wf._connector.fetch_page

    async def mock_fetch(url: str, max_chars=None):
        return {
            "url": url,
            "final_url": url,
            "title": "Sample Filing",
            "published_at": "2026-06-10",
            "text": "filing body",
            "truncated": False,
            "fetched_at": "2026-06-11T00:00:00Z",
        }

    wf._connector.fetch_page = mock_fetch
    try:
        r = client.get(
            "/web/fetch", headers=auth_headers, params={"url": "https://example.com/f"}
        )
        assert r.status_code == 200
        body = r.json()
        assert body["title"] == "Sample Filing"
        assert body["published_at"] == "2026-06-10"
        assert body["error"] is None
    finally:
        wf._connector.fetch_page = original


def test_web_fetch_endpoint_error_shape(client: TestClient, auth_headers: dict[str, str]) -> None:
    from inalpha_data.connectors import web_fetch as wf

    original = wf._connector.fetch_page

    async def mock_fetch(url: str, max_chars=None):
        return {"url": url, "error": "HTTP 403"}

    wf._connector.fetch_page = mock_fetch
    try:
        r = client.get(
            "/web/fetch", headers=auth_headers, params={"url": "https://example.com/x"}
        )
        assert r.status_code == 200
        assert r.json()["error"] == "HTTP 403"
    finally:
        wf._connector.fetch_page = original
