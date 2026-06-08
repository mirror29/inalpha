"""ddgs (Dux Distributed Global Search) web search connector.
Zero API key, MIT license. Backends: bing, brave, duckduckgo, google,
grokipedia, mojeek, yandex, yahoo, wikipedia.

pip install ddgs
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from inalpha_shared import get_logger

from ..config import get_data_settings

VENUE = "web"
_logger = get_logger(__name__)


class WebSearchConnector:
    """ddgs metasearch wrapper — sync lib wrapped via asyncio.to_thread.

    并发与超时治理（运维修复）：to_thread 本身没堵事件循环，真正的坑是
    backend="auto" 顺序试多引擎叠成 30s+ 长尾、以及一波并发（analyst 常 ~10 个
    并行查询）占满线程池后持 GIL 做 HTML 解析、把 async 事件循环饿死、/health 偶发
    超时。这里加：(1) Semaphore 限同时在飞数；(2) 整体 wait_for 超时上限，超时返 []。
    """

    def __init__(self) -> None:
        s = get_data_settings()
        self._engine_timeout = s.web_search_timeout_s
        self._overall_timeout = s.web_search_overall_timeout_s
        self._sem = asyncio.Semaphore(s.web_search_max_concurrency)

    async def fetch_search(
        self,
        query: str,
        backend: str = "auto",
        max_results: int = 10,
    ) -> list[dict[str, Any]]:
        """Text web search. Returns [{title, href, body}]."""
        # Detect Chinese: use bing backend for better Chinese results
        if backend == "auto":
            has_cjk = any('\u4e00' <= c <= '\u9fff' for c in query)
            backend = "bing" if has_cjk else "auto"
        return await self._run_guarded(
            _search_sync, kind="search", query=query, backend=backend, max_results=max_results
        )

    async def fetch_news(
        self,
        query: str,
        max_results: int = 10,
    ) -> list[dict[str, Any]]:
        """News search. Returns [{title, href, body}]."""
        return await self._run_guarded(
            _news_sync, kind="news", query=query, max_results=max_results
        )

    async def _run_guarded(
        self, fn: Callable[..., list[dict[str, Any]]], *, kind: str, **kwargs: Any
    ) -> list[dict[str, Any]]:
        """限并发 + 整体超时跑同步搜索。超时/异常一律返回 []（搜索是尽力而为的增强项）。"""
        async with self._sem:
            try:
                return await asyncio.wait_for(
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
                return []
            except Exception as exc:
                # 搜索是尽力而为的增强项:DDGS / to_thread / semaphore 等的罕见异常
                # 不能穿透到上层 analyst fan-out 把整条链搞崩,一律吞掉返回 []。
                _logger.warning(
                    "web_search_error",
                    kind=kind,
                    query=str(kwargs.get("query", ""))[:100],
                    error=str(exc),
                )
                return []

    async def close(self) -> None:
        return None


def _search_sync(
    query: str, backend: str, max_results: int, engine_timeout: int = 8
) -> list[dict[str, Any]]:
    try:
        from ddgs import DDGS
    except ImportError:
        _logger.warning("web_search_ddgs_not_installed", hint="pip install ddgs")
        return []
    try:
        with DDGS(timeout=engine_timeout) as ddgs:
            results = list(ddgs.text(query, backend=backend, max_results=max_results))
        return results
    except Exception as exc:
        _logger.warning("web_search_failed", query=query[:100], error=str(exc))
        return []


def _news_sync(
    query: str, max_results: int, engine_timeout: int = 8
) -> list[dict[str, Any]]:
    try:
        from ddgs import DDGS
    except ImportError:
        _logger.warning("web_search_ddgs_not_installed", hint="pip install ddgs")
        return []
    try:
        with DDGS(timeout=engine_timeout) as ddgs:
            results = list(ddgs.news(query, max_results=max_results))
        return results
    except Exception as exc:
        _logger.warning("web_search_news_failed", query=query[:100], error=str(exc))
        return []


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
