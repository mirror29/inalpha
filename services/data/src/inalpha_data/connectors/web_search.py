"""ddgs (Dux Distributed Global Search) web search connector.
Zero API key, MIT license. Backends: bing, brave, duckduckgo, google,
grokipedia, mojeek, yandex, yahoo, wikipedia.

pip install ddgs
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from inalpha_shared import get_logger

from ..config import get_data_settings
from ..schemas import WebSearchStatus

VENUE = "web"
_logger = get_logger(__name__)

_CJK_NEWS_HINT = (
    "ddgs.news 无中文财经源，已降级为网页搜索；"
    "A股市场级快讯请优先用 data.get_market_news"
)


def _has_cjk(query: str) -> bool:
    return any("一" <= c <= "鿿" for c in query)


@dataclass
class SearchOutcome:
    """单次搜索的结果 + 失败原因。

    金融 agent 的搜索"空结果"有两种完全不同的含义——"真没搜到"（可当弱证据）
    与"引擎故障 / 限速 / 超时"（不能当证据）。修复前两者都被吞成 []，上层
    无法区分；本类把 status 显式带回去。
    """

    results: list[dict[str, Any]] = field(default_factory=list)
    status: WebSearchStatus = "ok"
    error: str | None = None
    backend_used: str = ""
    hint: str | None = None


def _classify_exception(exc: BaseException) -> WebSearchStatus:
    """按异常类型名分类（不硬依赖 ddgs.exceptions 的 import）。

    坑：ddgs 空结果不是返回 []，而是抛 DDGSException("No results found.")——
    必须按消息识别为 no_results，否则会被误判成引擎故障。
    """
    if "no results" in str(exc).lower():
        return "no_results"
    name = type(exc).__name__
    if "Ratelimit" in name:
        return "rate_limited"
    if "Timeout" in name:
        return "timeout"
    return "engine_error"


def _better(a: SearchOutcome, b: SearchOutcome) -> SearchOutcome:
    """两次尝试取较好者：有结果 > no_results > 其它失败。"""
    if b.results:
        return b
    if a.results:
        return a
    if b.status == "no_results":
        return b
    return a if a.status == "no_results" else b


class WebSearchConnector:
    """ddgs metasearch wrapper — sync lib wrapped via asyncio.to_thread.

    并发与超时治理（运维修复）：to_thread 本身没堵事件循环，真正的坑是
    backend="auto" 顺序试多引擎叠成 30s+ 长尾、以及一波并发（analyst 常 ~10 个
    并行查询）占满线程池后持 GIL 做 HTML 解析、把 async 事件循环饿死、/health 偶发
    超时。这里加：(1) Semaphore 限同时在飞数；(2) 整体 wait_for 超时上限。
    """

    def __init__(self) -> None:
        s = get_data_settings()
        self._engine_timeout = s.web_search_timeout_s
        self._overall_timeout = s.web_search_overall_timeout_s
        self._sem = asyncio.Semaphore(s.web_search_max_concurrency)
        # 可靠性补丁：短 TTL 缓存（只存 ok 非空，TTL=0 关）+ auto 失败换 backend 重试一次
        # ——深扫一轮 10+ 次搜索会放大 429 假阴性（外观与"没证据"无法区分）
        self._cache_ttl = s.web_search_cache_ttl_s
        self._cache: dict[tuple[Any, ...], tuple[float, SearchOutcome]] = {}

    async def fetch_search(
        self,
        query: str,
        backend: str = "auto",
        max_results: int = 10,
    ) -> SearchOutcome:
        """Text web search. results = [{title, href, body}]."""
        # Detect Chinese: use bing backend for better Chinese results
        auto_requested = backend == "auto"
        if auto_requested and _has_cjk(query):
            backend = "bing"

        key = ("search", query, backend, max_results)
        cached = self._cache_get(key)
        if cached is not None:
            return cached

        outcome = await self._search_with_fallback(
            query=query,
            backend=backend,
            max_results=max_results,
            allow_fallback=auto_requested,
        )
        self._cache_put(key, outcome)
        return outcome

    async def fetch_news(
        self,
        query: str,
        max_results: int = 10,
    ) -> SearchOutcome:
        """News search. results = [{title, href, body}]."""
        # cache key check 必须在 CJK 分支之前——否则中文 news（最高频路径，
        # deep_dive fan-out 里 sentiment/macro/web analyst 并行用同一 query）
        # 完全绕开 60s 缓存，每路独立打源站。query 本身已区分 CJK 与否，共用 key 安全。
        key = ("news", query, max_results)
        cached = self._cache_get(key)
        if cached is not None:
            return cached

        # ddgs.news 的聚合源没有中文财经内容（中文 query 实测必返空），试了也是
        # 白烧一个 overall_timeout——直接降级为网页搜索（bing 中文新闻 query 常有
        # 可用结果），并恒带 hint 把 agent 引向市场级快讯工具。
        # allow_fallback=False（刻意）：CJK news 已是"兜底中的兜底"，bing 失败再换
        # duckduckgo 会把最坏耗时翻到 40s（deep_dive fan-out 同 query 并发时进一步
        # 放大），不值得——快速失败带 status，让 orchestrator 走 data.get_market_news
        # （A股专业快讯源，prompt 已有此降级规则）。
        if _has_cjk(query):
            outcome = await self._search_with_fallback(
                query=query, backend="bing", max_results=max_results, allow_fallback=False
            )
            outcome.backend_used = f"{outcome.backend_used}(text-fallback-for-cjk-news)"
            outcome.hint = _CJK_NEWS_HINT
        else:
            outcome = await self._run_guarded(
                _news_sync, kind="news", query=query, max_results=max_results
            )
            outcome.backend_used = "news"

        # _cache_put 只缓存 status=="ok" 非空结果（失败不污染缓存）
        self._cache_put(key, outcome)
        return outcome

    async def _search_with_fallback(
        self,
        *,
        query: str,
        backend: str,
        max_results: int,
        allow_fallback: bool,
    ) -> SearchOutcome:
        outcome = await self._run_guarded(
            _search_sync, kind="search", query=query, backend=backend, max_results=max_results
        )
        outcome.backend_used = backend
        # 429 / 超时 / 引擎抽风的空结果换一个引擎兜一次（duckduckgo 与 bing 互为备份）。
        # timeout 也换：本地网络对某个引擎连不上时另一个常能救回（本次根因之一）。
        if not outcome.results and allow_fallback:
            fallback = "duckduckgo" if backend == "bing" else "bing"
            _logger.info(
                "web_search_fallback_retry",
                query=query[:100],
                status=outcome.status,
                fallback=fallback,
            )
            retry = await self._run_guarded(
                _search_sync, kind="search", query=query, backend=fallback,
                max_results=max_results,
            )
            retry.backend_used = fallback
            outcome = _better(outcome, retry)
        return outcome

    def _cache_get(self, key: tuple[Any, ...]) -> SearchOutcome | None:
        if self._cache_ttl <= 0:
            return None
        hit = self._cache.get(key)
        if hit is None:
            return None
        expires, outcome = hit
        if time.monotonic() > expires:
            self._cache.pop(key, None)
            return None
        return outcome

    def _cache_put(self, key: tuple[Any, ...], outcome: SearchOutcome) -> None:
        # 只缓存 ok 非空——失败可能是限速假阴性，缓存会把 0 结果钉死一个 TTL
        if self._cache_ttl <= 0 or outcome.status != "ok" or not outcome.results:
            return
        if len(self._cache) > 512:
            self._cache.clear()
        self._cache[key] = (time.monotonic() + self._cache_ttl, outcome)

    async def _run_guarded(
        self, fn: Callable[..., list[dict[str, Any]]], *, kind: str, **kwargs: Any
    ) -> SearchOutcome:
        """限并发 + 整体超时跑同步搜索。

        失败不上抛（搜索是尽力而为的增强项，不能穿透到上层 analyst fan-out
        把整条链搞崩），但失败原因经 SearchOutcome.status 带回——静默吞成
        空数组会让"引擎故障"与"真没结果"无法区分。
        """
        async with self._sem:
            try:
                results = await asyncio.wait_for(
                    asyncio.to_thread(fn, engine_timeout=self._engine_timeout, **kwargs),
                    timeout=self._overall_timeout,
                )
            except TimeoutError:
                _logger.warning(
                    "web_search_timeout",
                    kind=kind,
                    query=str(kwargs.get("query", ""))[:100],
                    timeout_s=self._overall_timeout,
                )
                return SearchOutcome(
                    status="timeout",
                    error=f"search timed out after {self._overall_timeout}s",
                )
            except asyncio.CancelledError:
                # Py3.8+ CancelledError 非 Exception 子类——必须显式重抛，否则会穿过
                # 下面的 except Exception 后到达 `if not results`（results 未绑定→
                # NameError 掩盖取消信号），上层无法正确响应 tool call 取消。
                raise
            except Exception as exc:
                status = _classify_exception(exc)
                _logger.warning(
                    "web_search_error",
                    kind=kind,
                    query=str(kwargs.get("query", ""))[:100],
                    status=status,
                    error=str(exc),
                )
                return SearchOutcome(status=status, error=str(exc))
        if not results:
            return SearchOutcome(status="no_results", error="No results found.")
        return SearchOutcome(results=results)

    async def close(self) -> None:
        return None


def _search_sync(
    query: str, backend: str, max_results: int, engine_timeout: int = 8
) -> list[dict[str, Any]]:
    try:
        from ddgs import DDGS
    except ImportError as exc:
        raise RuntimeError("ddgs not installed; pip install ddgs") from exc
    with DDGS(timeout=engine_timeout) as ddgs:
        return list(ddgs.text(query, backend=backend, max_results=max_results))


def _news_sync(
    query: str, max_results: int, engine_timeout: int = 8
) -> list[dict[str, Any]]:
    try:
        from ddgs import DDGS
    except ImportError as exc:
        raise RuntimeError("ddgs not installed; pip install ddgs") from exc
    with DDGS(timeout=engine_timeout) as ddgs:
        return list(ddgs.news(query, max_results=max_results))


# ---------- module-level singleton ----------

_connector: WebSearchConnector | None = None


def init_connector() -> WebSearchConnector:
    """启动时调一次。ddgs 无 API key 需要。"""
    global _connector
    if _connector is not None:
        raise RuntimeError("WebSearch connector already initialized")
    _connector = WebSearchConnector()
    return _connector


async def close_connector() -> None:
    global _connector
    if _connector is None:
        return
    await _connector.close()
    _connector = None


def get_connector() -> WebSearchConnector:
    if _connector is None:
        raise RuntimeError("WebSearch connector not initialized; call init_connector() first")
    return _connector
