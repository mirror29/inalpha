"""web_search 可靠性补丁测试：短 TTL 缓存 + auto 空结果换 backend 重试。"""
from __future__ import annotations

from typing import Any

import pytest

from inalpha_data.connectors.web_search import WebSearchConnector

pytestmark = pytest.mark.anyio

_HIT = [{"title": "t", "href": "https://example.com", "body": "b"}]


def _patch_guarded(conn: WebSearchConnector, script: list[list[dict[str, Any]]]):
    """让 _run_guarded 按脚本依次返回，并记录每次调用的 backend。"""
    calls: list[str | None] = []

    async def fake(fn, *, kind: str, **kwargs: Any):
        calls.append(kwargs.get("backend"))
        return script.pop(0) if script else []

    conn._run_guarded = fake  # type: ignore[method-assign]
    return calls


async def test_cache_hit_within_ttl() -> None:
    conn = WebSearchConnector()
    calls = _patch_guarded(conn, [list(_HIT)])
    r1 = await conn.fetch_search("nvidia hbm supply", backend="bing")
    r2 = await conn.fetch_search("nvidia hbm supply", backend="bing")
    assert r1 == r2 == _HIT
    assert len(calls) == 1  # 第二次走缓存


async def test_empty_results_not_cached() -> None:
    conn = WebSearchConnector()
    calls = _patch_guarded(conn, [[], [], list(_HIT)])
    r1 = await conn.fetch_search("x", backend="bing")  # 显式 backend：空、不重试、不缓存
    assert r1 == []
    assert len(calls) == 1
    await conn.fetch_search("x", backend="bing")  # 不命中缓存 → 再真打一次
    assert len(calls) == 2


async def test_auto_empty_retries_with_fallback_backend() -> None:
    conn = WebSearchConnector()
    calls = _patch_guarded(conn, [[], list(_HIT)])
    out = await conn.fetch_search("产业链 卡点", backend="auto")  # CJK → bing
    assert out == _HIT
    assert calls == ["bing", "duckduckgo"]  # 空结果换引擎兜了一次


async def test_explicit_backend_no_retry() -> None:
    conn = WebSearchConnector()
    calls = _patch_guarded(conn, [[]])
    out = await conn.fetch_search("anything", backend="google")
    assert out == []
    assert calls == ["google"]


async def test_news_cached_too() -> None:
    conn = WebSearchConnector()
    calls = _patch_guarded(conn, [list(_HIT)])
    await conn.fetch_news("tsmc capacity")
    await conn.fetch_news("tsmc capacity")
    assert len(calls) == 1


async def test_cache_disabled_when_ttl_zero() -> None:
    conn = WebSearchConnector()
    conn._cache_ttl = 0
    calls = _patch_guarded(conn, [list(_HIT), list(_HIT)])
    await conn.fetch_search("q", backend="bing")
    await conn.fetch_search("q", backend="bing")
    assert len(calls) == 2
