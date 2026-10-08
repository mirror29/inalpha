"""Forward archive regression tests without production writes."""
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import pytest

from inalpha_data import event_archive
from inalpha_data.event_models import RawEventIngestRequest
from inalpha_data.news_models import NewsItem, NewsProviderStatus, NewsResponse

pytestmark = pytest.mark.anyio


async def test_archive_ingests_each_source_and_preserves_first_observation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """归档不漏掉低频来源，且老发布日期不能变成历史 accepted_at。"""
    observed = datetime(2026, 7, 28, tzinfo=UTC)
    batches = [
        NewsResponse(
            market="crypto", fetched_at=observed,
            providers=[NewsProviderStatus(provider=f"rss:{source}", status="ok", item_count=1, fetched_at=observed)],
            items=[NewsItem(
                title=source, source_name=source, source_id=source,
                published_at=datetime(2020, 1, 1, tzinfo=UTC), fetched_at=observed,
            )],
        )
        for source in ("busy", "official")
    ]

    class Router:
        async def fetch_archive_batches(self, query):
            assert query.limit == 50
            return batches

    @asynccontextmanager
    async def connection():
        yield object()

    ingested: list[RawEventIngestRequest] = []

    async def ingest(conn, request):
        ingested.append(request)

    monkeypatch.setattr(event_archive, "get_router", lambda: Router())
    monkeypatch.setattr(event_archive, "get_conn", connection)
    monkeypatch.setattr(event_archive.store, "ingest_raw_event", ingest)
    started = datetime.now(UTC)
    await event_archive.EventArchiveScheduler(enabled=True, interval_s=900)._tick()
    assert [request.source for request in ingested] == ["busy", "official"]
    for request in ingested:
        assert request.first_seen_at == observed
        assert request.accepted_at >= started
        assert request.accepted_at > request.source_valid_at
        assert request.collector_version == "selected-news-forward@1"
        assert request.policy_version == "first-seen-only-v1"
