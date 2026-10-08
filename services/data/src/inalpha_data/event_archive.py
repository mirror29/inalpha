"""Forward-only archive for selected crypto news and official announcements."""

from __future__ import annotations

import asyncio
import hashlib
import traceback
from datetime import UTC, datetime
from pathlib import Path

from inalpha_shared import get_logger
from inalpha_shared.db import get_conn

from .connectors.news import get_router
from .event_models import RawEventIngestRequest
from .news_models import NewsQuery, NewsResponse
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
                _log_failure("event_archive_tick_failed", exc)
            await asyncio.sleep(self._interval_s)

    async def _tick(self) -> None:
        batches = await get_router().fetch_archive_batches(NewsQuery(market="crypto", limit=50))
        for response in batches:
            status = response.providers[0]
            _logger.info(
                "event_archive_source_fetched",
                provider=status.provider,
                status=status.status,
                fetched_items=status.item_count,
                archive_items=len(response.items),
            )
            try:
                await self._ingest_batch(response)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                _log_failure("event_archive_source_failed", exc, provider=status.provider)
                continue
            _logger.info(
                "event_archive_source_completed",
                provider=status.provider,
                archive_items=len(response.items),
            )

    async def _ingest_batch(self, response: NewsResponse) -> None:
        """在单独事务中写入来源批次；失败回滚后由后续轮询幂等重试。"""
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


def _log_failure(event: str, exc: Exception, *, provider: str | None = None) -> None:
    """保留故障代码位置，不记录异常内容、源码行或绝对路径。"""
    frames = [
        {
            "file": Path(frame.f_code.co_filename).name,
            "function": frame.f_code.co_name,
            "line": line,
        }
        for frame, line in traceback.walk_tb(exc.__traceback__)
    ]
    _logger.warning(
        event,
        provider=provider,
        error_type=type(exc).__name__,
        code_locations=frames[-6:],
    )


__all__ = ["EventArchiveScheduler"]
