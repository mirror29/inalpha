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
    await event_archive.EventArchiveScheduler(enabled=True, interval_s=900)._collect_once()
    assert [request.source for request in ingested] == ["busy", "official"]
    for request in ingested:
        assert request.first_seen_at == observed
        assert request.accepted_at >= started
        assert request.accepted_at > request.source_valid_at
        assert request.collector_version == "selected-news-forward@1"
        assert request.policy_version == "first-seen-only-v1"


async def test_failed_tick_logs_code_locations_without_exception_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """故障保留文件/函数/行号，但不泄漏异常内容、源码行或绝对路径。"""
    import asyncio

    scheduler = event_archive.EventArchiveScheduler(enabled=True, interval_s=900)
    logs = []

    async def fail_tick():
        raise RuntimeError("private-upstream-payload-and-credential")

    async def stop_after_tick(seconds):
        raise asyncio.CancelledError

    class Logger:
        def warning(self, event, **fields):
            logs.append((event, fields))

    monkeypatch.setattr(scheduler, "_tick", fail_tick)
    monkeypatch.setattr(event_archive, "_logger", Logger())
    monkeypatch.setattr(event_archive.asyncio, "sleep", stop_after_tick)
    with pytest.raises(asyncio.CancelledError):
        await scheduler._loop()
    event, fields = logs[0]
    assert event == "event_archive_tick_failed"
    assert fields["error_type"] == "RuntimeError"
    assert fields["code_locations"][-1]["function"] == "fail_tick"
    assert fields["code_locations"][-1]["file"] == "test_event_archive.py"
    assert fields["code_locations"][-1]["line"] > 0
    assert "private-upstream-payload-and-credential" not in repr(logs)
    assert all("/" not in frame["file"] for frame in fields["code_locations"])
    assert all(set(frame) == {"file", "function", "line"} for frame in fields["code_locations"])


@pytest.mark.integration
@pytest.mark.usefixtures("db_pool")
async def test_failed_source_rolls_back_and_healthy_source_retries_idempotently(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """真实事务故障不跳过后续来源；重复轮询不重复原文或 outbox。"""
    from uuid import uuid4

    marker = uuid4().hex
    broken, healthy = f"broken-{marker}", f"healthy-{marker}"
    observed = datetime.now(UTC)
    batches = [
        NewsResponse(
            market="crypto", fetched_at=observed,
            providers=[NewsProviderStatus(provider=f"rss:{source}", status="ok",
                                          item_count=1, fetched_at=observed)],
            items=[NewsItem(title=source, source_name=source, source_id="one",
                            published_at=observed, fetched_at=observed)],
        )
        for source in (broken, healthy)
    ]

    class Router:
        async def fetch_archive_batches(self, query):
            return batches

    original_ingest = event_archive.store.ingest_raw_event

    async def ingest_then_fail(conn, request):
        result = await original_ingest(conn, request)
        if request.source == broken:
            raise ValueError("private-broken-payload")
        return result

    failures = []

    class Logger:
        def info(self, event, **fields):
            pass

        def warning(self, event, **fields):
            failures.append((event, fields))

    monkeypatch.setattr(event_archive, "get_router", lambda: Router())
    monkeypatch.setattr(event_archive, "_logger", Logger())
    monkeypatch.setattr(event_archive.store, "ingest_raw_event", ingest_then_fail)
    scheduler = event_archive.EventArchiveScheduler(enabled=True, interval_s=900)
    await scheduler._tick()
    await scheduler._tick()
    async with event_archive.get_conn() as conn:
        rows = await (await conn.execute(
            "SELECT source, count(*) AS count FROM raw_market_events "
            "WHERE source=ANY(%s) GROUP BY source", ([broken, healthy],),
        )).fetchall()
        jobs = await (await conn.execute(
            "SELECT count(*) AS count FROM event_extraction_jobs j "
            "JOIN raw_market_events r ON r.event_id=j.raw_event_id WHERE r.source=%s",
            (healthy,),
        )).fetchone()
    assert rows == [{"source": healthy, "count": 1}]
    assert jobs["count"] == 1
    assert len(failures) == 2
    assert all(event == "event_archive_source_failed" for event, _ in failures)
    assert all(fields["provider"] == f"rss:{broken}" for _, fields in failures)
    assert "private-broken-payload" not in repr(failures)


async def test_source_write_cancellation_stops_remaining_batches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """服务取消必须传播，不能被来源故障隔离吞掉。"""
    import asyncio

    observed = datetime.now(UTC)
    response = NewsResponse(
        market="crypto", fetched_at=observed,
        providers=[NewsProviderStatus(provider="rss:test", status="ok", item_count=0,
                                      fetched_at=observed)],
    )

    class Router:
        async def fetch_archive_batches(self, query):
            return [response, response]

    calls = []

    async def cancel(batch):
        calls.append(batch)
        raise asyncio.CancelledError

    scheduler = event_archive.EventArchiveScheduler(enabled=True, interval_s=900)
    monkeypatch.setattr(event_archive, "get_router", lambda: Router())
    monkeypatch.setattr(scheduler, "_ingest_batch", cancel)
    with pytest.raises(asyncio.CancelledError):
        await scheduler._collect_once()
    assert len(calls) == 1


@pytest.mark.integration
@pytest.mark.usefixtures("db_pool")
async def test_concurrent_workers_poll_once_and_next_worker_can_take_over(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """真实数据库锁隔离并发轮询，正常关闭连接后另一 worker 可接手。"""
    import asyncio

    entered, release = asyncio.Event(), asyncio.Event()
    calls = []

    class Router:
        async def fetch_archive_batches(self, query):
            calls.append(query)
            entered.set()
            await release.wait()
            return []

    monkeypatch.setattr(event_archive, "get_router", lambda: Router())
    first = event_archive.EventArchiveScheduler(enabled=True, interval_s=900)
    second = event_archive.EventArchiveScheduler(enabled=True, interval_s=900)
    owner = asyncio.create_task(first._tick())
    try:
        await asyncio.wait_for(entered.wait(), timeout=5)
        await asyncio.wait_for(second._tick(), timeout=5)
        assert len(calls) == 1
    finally:
        release.set()
        await owner
    await asyncio.wait_for(second._tick(), timeout=5)
    assert len(calls) == 2


@pytest.mark.integration
@pytest.mark.usefixtures("db_pool")
async def test_cancelled_collector_releases_lock_for_next_worker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """取消持锁采集后，数据库连接释放锁，不遗留采集阻塞。"""
    import asyncio

    entered = asyncio.Event()
    calls = []

    class Router:
        async def fetch_archive_batches(self, query):
            calls.append(query)
            if len(calls) == 1:
                entered.set()
                await asyncio.Event().wait()
            return []

    monkeypatch.setattr(event_archive, "get_router", lambda: Router())
    scheduler = event_archive.EventArchiveScheduler(enabled=True, interval_s=900)
    owner = asyncio.create_task(scheduler._tick())
    await asyncio.wait_for(entered.wait(), timeout=5)
    owner.cancel()
    with pytest.raises(asyncio.CancelledError):
        await owner
    await asyncio.wait_for(scheduler._tick(), timeout=5)
    assert len(calls) == 2
