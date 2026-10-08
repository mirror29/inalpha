"""RSS/Atom 财经新闻 provider。"""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import feedparser
import httpx

from ...news_models import NewsItem, NewsQuery
from .base import ProviderResult
from .feed_models import FeedDefinition, parse_entry


class RssFeedProvider:
    """单一 RSS/Atom feed；provider 状态精确到来源。"""

    def __init__(self, definition: FeedDefinition, *, timeout_s: float) -> None:
        self.definition = definition
        self.name = f"rss:{definition.id}"
        self.archive_only = definition.archive_only
        self.coverage = "snapshot_only"
        self._client = httpx.AsyncClient(
            timeout=timeout_s,
            trust_env=False,
            follow_redirects=True,
            headers={"User-Agent": "Inalpha/0.2 financial-news"},
        )
        self._etag: str | None = None
        self._last_modified: str | None = None
        self._cached: list[NewsItem] = []

    def supports(self, query: NewsQuery) -> bool:
        """Crypto feed 只覆盖无标的的市场级媒体消息。"""
        return bool(
            query.market == "crypto"
            and not query.symbol
            and (not query.kinds or "media" in query.kinds)
            and (not query.language or query.language == self.definition.language)
        )

    async def fetch(self, query: NewsQuery) -> ProviderResult:
        """条件请求 feed，并标准化条目时间和来源。"""
        fetched_at = datetime.now(UTC)
        if query.market != "crypto" or query.symbol:
            return ProviderResult(self.name, "unsupported", fetched_at=fetched_at)
        headers = {}
        if self._etag:
            headers["If-None-Match"] = self._etag
        if self._last_modified:
            headers["If-Modified-Since"] = self._last_modified
        try:
            response = await self._client.get(self.definition.url, headers=headers)
            if response.status_code == 304:
                return ProviderResult(
                    self.name,
                    "ok" if self._cached else "no_results",
                    fetched_at=fetched_at,
                    items=self._cached,
                    coverage="snapshot_only",
                )
            response.raise_for_status()
            parsed = await asyncio.to_thread(feedparser.parse, response.content)
            if parsed.bozo and not parsed.entries:
                raise ValueError(f"malformed feed: {parsed.bozo_exception}")
            self._etag = response.headers.get("etag")
            self._last_modified = response.headers.get("last-modified")
            self._cached = [
                parse_entry(item, self.definition, query, fetched_at) for item in parsed.entries
            ]
            return ProviderResult(
                self.name,
                "ok" if self._cached else "no_results",
                fetched_at=fetched_at,
                items=self._cached,
                coverage="snapshot_only",
            )
        except httpx.TimeoutException as exc:
            return ProviderResult(self.name, "timeout", fetched_at=fetched_at, error=str(exc))
        except httpx.HTTPStatusError as exc:
            status = "rate_limited" if exc.response.status_code == 429 else "upstream_error"
            return ProviderResult(self.name, status, fetched_at=fetched_at, error=str(exc))
        except Exception as exc:
            return ProviderResult(
                self.name, "upstream_error", fetched_at=fetched_at, error=str(exc)
            )

    async def close(self) -> None:
        """关闭 feed HTTP client。"""
        await self._client.aclose()
