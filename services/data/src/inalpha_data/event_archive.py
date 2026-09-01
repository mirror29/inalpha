"""Forward-only archive for selected crypto news and official announcements."""

from __future__ import annotations

import asyncio
import hashlib
from datetime import UTC, datetime

from inalpha_shared import get_logger
from inalpha_shared.db import get_conn

from .connectors.news import get_router
from .event_models import RawEventIngestRequest
from .news_models import NewsQuery
from .storage import events as store

_logger = get_logger(__name__)


class EventArchiveScheduler:
    """Poll selected snapshot-only feeds and preserve their first observed versions."""

    def __init__(self, *, enabled: bool, interval_s: int) -> None:
        self._enabled = enabled
        self._interval_s = interval_s
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        """Start forward accumulation when explicitly enabled."""
        if not self._enabled:
            _logger.info("event_archive_disabled")
            return
        if self._task is None:
            self._task = asyncio.create_task(self._loop(), name="event-archive-scheduler")
            _logger.info("event_archive_started", interval_s=self._interval_s)

    async def stop(self) -> None:
        """Cancel the archive loop without delaying service shutdown."""
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

    async def _loop(self) -> None:
        while True:
            try:
                await self._tick()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                _logger.warning("event_archive_tick_failed", error=str(exc))
            await asyncio.sleep(self._interval_s)

    async def _tick(self) -> None:
        response = await get_router().fetch(NewsQuery(market="crypto", limit=50))
        async with get_conn() as conn:
            for item in response.items:
                observed_at = item.accepted_at or item.fetched_at or response.fetched_at
                source_event_id = (
                    item.source_id
                    or item.link
                    or hashlib.sha256(
                        f"{item.source_name}\0{item.title}\0{item.published_at}".encode()
                    ).hexdigest()
                )
                request = RawEventIngestRequest(
                    source=item.source_name or item.publisher or "crypto_news",
                    source_event_id=source_event_id,
                    title=item.title,
                    content=item.summary,
                    url=item.link or None,
                    raw_payload={
                        "kind": item.kind,
                        "market": item.market,
                        "symbols": item.symbols,
                        "alternative_sources": item.alternative_sources,
                    },
                    source_valid_at=item.published_at,
                    claimed_published_at=item.published_at,
                    first_seen_at=observed_at,
                    fetched_at=item.fetched_at or response.fetched_at,
                    accepted_at=max(observed_at, datetime.now(UTC)),
                    collector_version="selected-news-forward@1",
                    policy_version="first-seen-only-v1",
                    source_tier=item.source_tier,
                )
                await store.ingest_raw_event(conn, request)


__all__ = ["EventArchiveScheduler"]
