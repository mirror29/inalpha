"""web_search 可靠性补丁测试：短 TTL 缓存 + auto 失败换 backend 重试。"""
from __future__ import annotations

from typing import Any

import pytest

from inalpha_data.connectors.web_search import SearchOutcome, WebSearchConnector

pytestmark = pytest.mark.anyio

_HIT = [{"title": "t", "href": "https://example.com", "body": "b"}]


def _patch_guarded(conn: WebSearchConnector, script: list[SearchOutcome]):
    """让 _run_guarded 按脚本依次返回，并记录每次调用的 backend。"""
    calls: list[str | None] = []

    async def fake(fn, *, kind: str, **kwargs: Any):
        calls.append(kwargs.get("backend"))
        return script.pop(0) if script else SearchOutcome(status="no_results")

    conn._run_guarded = fake  # type: ignore[method-assign]
    return calls


def _hit() -> SearchOutcome:
    return SearchOutcome(results=list(_HIT))


def _miss(status: str = "no_results") -> SearchOutcome:
    return SearchOutcome(status=status)


async def test_cache_hit_within_ttl() -> None:
    conn = WebSearchConnector()
    calls = _patch_guarded(conn, [_hit()])
    r1 = await conn.fetch_search("nvidia hbm supply", backend="bing")
    r2 = await conn.fetch_search("nvidia hbm supply", backend="bing")
    assert r1.results == r2.results == _HIT
    assert len(calls) == 1  # 第二次走缓存


async def test_failed_outcome_not_cached() -> None:
    conn = WebSearchConnector()
    calls = _patch_guarded(conn, [_miss(), _miss(), _hit()])
    r1 = await conn.fetch_search("x", backend="bing")  # 显式 backend：失败、不重试、不缓存
    assert r1.results == []
    assert r1.status == "no_results"
    assert len(calls) == 1
    await conn.fetch_search("x", backend="bing")  # 不命中缓存 → 再真打一次
    assert len(calls) == 2


async def test_auto_empty_retries_with_fallback_backend() -> None:
    conn = WebSearchConnector()
    calls = _patch_guarded(conn, [_miss(), _hit()])
    out = await conn.fetch_search("产业链 卡点", backend="auto")  # CJK → bing
    assert out.results == _HIT
    assert out.backend_used == "duckduckgo"
    assert calls == ["bing", "duckduckgo"]  # 失败换引擎兜了一次


async def test_auto_timeout_also_retries_with_fallback() -> None:
    """timeout 也换引擎兜一次——本地网络对单引擎连不上时另一个常能救回（本次根因）。"""
    conn = WebSearchConnector()
    calls = _patch_guarded(conn, [_miss("timeout"), _hit()])
    out = await conn.fetch_search("产业链 卡点", backend="auto")
    assert out.results == _HIT
    assert calls == ["bing", "duckduckgo"]


async def test_explicit_backend_no_retry() -> None:
    conn = WebSearchConnector()
    calls = _patch_guarded(conn, [_miss()])
    out = await conn.fetch_search("anything", backend="google")
    assert out.results == []
    assert calls == ["google"]


async def test_news_cached_too() -> None:
    conn = WebSearchConnector()
    calls = _patch_guarded(conn, [_hit()])
    await conn.fetch_news("tsmc capacity")
    await conn.fetch_news("tsmc capacity")
    assert len(calls) == 1


async def test_cache_disabled_when_ttl_zero() -> None:
    conn = WebSearchConnector()
    conn._cache_ttl = 0
    calls = _patch_guarded(conn, [_hit(), _hit()])
    await conn.fetch_search("q", backend="bing")
    await conn.fetch_search("q", backend="bing")
    assert len(calls) == 2


async def test_double_failure_keeps_more_informative_status() -> None:
    """两次都失败时取信息量较高者：no_results 优于 timeout/engine_error。"""
    conn = WebSearchConnector()
    _patch_guarded(conn, [_miss("timeout"), _miss("no_results")])
    out = await conn.fetch_search("产业链 卡点", backend="auto")
    assert out.status == "no_results"
