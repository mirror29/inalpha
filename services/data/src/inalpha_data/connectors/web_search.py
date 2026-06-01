"""ddgs (Dux Distributed Global Search) web search connector.
Zero API key, MIT license. Backends: bing, brave, duckduckgo, google,
grokipedia, mojeek, yandex, yahoo, wikipedia.

pip install ddgs
"""

from __future__ import annotations

import asyncio
from typing import Any

from inalpha_shared import get_logger

VENUE = "web"
_logger = get_logger(__name__)


class WebSearchConnector:
    """ddgs metasearch wrapper — sync lib wrapped via asyncio.to_thread."""

    def __init__(self) -> None:
        pass  # ddgs is stateless, instantiated per call

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
        return await asyncio.to_thread(
            _search_sync, query=query, backend=backend, max_results=max_results
        )

    async def fetch_news(
        self,
        query: str,
        max_results: int = 10,
    ) -> list[dict[str, Any]]:
        """News search. Returns [{title, href, body}]."""
        return await asyncio.to_thread(
            _news_sync, query=query, max_results=max_results
        )

    async def close(self) -> None:
        return None


def _search_sync(query: str, backend: str, max_results: int) -> list[dict[str, Any]]:
    try:
        from ddgs import DDGS
    except ImportError:
        _logger.warning("web_search_ddgs_not_installed", hint="pip install ddgs")
        return []
    try:
        with DDGS(timeout=15) as ddgs:
            results = list(ddgs.text(query, backend=backend, max_results=max_results))
        return results
    except Exception as exc:
        _logger.warning("web_search_failed", query=query[:100], error=str(exc))
        return []


def _news_sync(query: str, max_results: int) -> list[dict[str, Any]]:
    try:
        from ddgs import DDGS
    except ImportError:
        _logger.warning("web_search_ddgs_not_installed", hint="pip install ddgs")
        return []
    try:
        with DDGS(timeout=15) as ddgs:
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
