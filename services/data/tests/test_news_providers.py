"""统一财经新闻层测试。"""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from inalpha_data.connectors.news.base import ProviderResult
from inalpha_data.connectors.news.dedupe import filter_and_dedupe
from inalpha_data.connectors.news.feed_models import FeedDefinition
from inalpha_data.connectors.news.hkex import HkexNewsProvider
from inalpha_data.connectors.news.hkex_parser import parse_rows
from inalpha_data.connectors.news.legacy import YahooNewsProvider
from inalpha_data.connectors.news.market_proxy import YahooMarketNewsProvider
from inalpha_data.connectors.news.router import NewsRouter
from inalpha_data.connectors.news.rss import RssFeedProvider
from inalpha_data.connectors.news.sec import SecNewsProvider
from inalpha_data.connectors.news.sec_parser import parse_submissions
from inalpha_data.news_models import NewsItem, NewsQuery

pytestmark = pytest.mark.anyio


async def test_router_times_out_provider_without_losing_fast_results() -> None:
    """单一长尾 provider 不得阻塞或吞掉已成功来源。"""
    class Provider:
        coverage = "snapshot_only"

        def __init__(self, name: str, *, slow: bool = False) -> None:
            self.name = name
            self.slow = slow

        def supports(self, query: NewsQuery) -> bool:
            return True

        async def fetch(self, query: NewsQuery):
            if self.slow:
                await asyncio.sleep(1)
            return ProviderResult(
                self.name,
                "ok",
                items=[NewsItem(
                    title=self.name,
                    published_at=datetime(2026, 7, 28, tzinfo=UTC),
                    source_name=self.name,
                )],
            )

        async def close(self) -> None:
            pass

    router = NewsRouter([Provider("yfinance"), Provider("us", slow=True)], timeout_s=0.01)
    result = await router.fetch(NewsQuery(market="us", symbol="AAPL"))
    assert [item.title for item in result.items] == ["yfinance"]
    assert {status.provider: status.status for status in result.providers} == {
        "yfinance": "ok",
        "us": "timeout",
    }
    assert result.is_partial is True


def test_sec_submissions_are_official_disclosures() -> None:
    query = NewsQuery(market="us", symbol="AAPL", as_of="2026-07-01T00:00:00Z")
    payload = {
        "name": "Apple Inc.",
        "filings": {"recent": {
            "accessionNumber": ["0000320193-26-000001", "0000320193-26-000002"],
            "acceptanceDateTime": ["2026-06-30T18:01:02Z", "2026-07-02T18:01:02Z"],
            "filingDate": ["2026-06-30", "2026-07-02"],
            "form": ["8-K", "10-Q"],
            "primaryDocument": ["aapl-8k.htm", "aapl-10q.htm"],
        }},
    }
    items = filter_and_dedupe(
        parse_submissions(payload, query, datetime.now(UTC), "0000320193"), query
    )
    assert len(items) == 1
    assert items[0].kind == "disclosure"
    assert items[0].source_tier == "official"
    assert "/320193/000032019326000001/aapl-8k.htm" in items[0].link


def test_hkex_rows_dedupe_languages_and_convert_timezone() -> None:
    query = NewsQuery(market="hk", symbol="0700.HK")
    rows = [
        {"NEWS_ID": "1", "TITLE": "Annual Results", "DATE_TIME": "28/07/2026 18:30",
         "FILE_LINK": "/listedco/listconews/sehk/2026/1.pdf", "_language": "en-HK"},
        {"NEWS_ID": "1", "TITLE": "全年業績", "DATE_TIME": "28/07/2026 18:30",
         "FILE_LINK": "/listedco/listconews/sehk/2026/1c.pdf", "_language": "zh-HK"},
    ]
    items = parse_rows(rows, query, datetime.now(UTC))
    assert len(items) == 1
    assert items[0].published_at == datetime(2026, 7, 28, 10, 30, tzinfo=UTC)


async def test_hkex_provider_respects_language_and_hong_kong_date_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """HKEX 只查目标语言，并按香港自然日构造 PIT 查询窗口。"""
    seen_languages: list[str] = []
    seen_params: dict[str, list[str]] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal seen_params
        if request.url.path.endswith("/search/prefix.do"):
            return httpx.Response(
                200,
                text='callback({"stockInfo":[{"code":"700","stockId":"42"}]});',
            )
        seen_params = parse_qs(urlsplit(str(request.url)).query)
        seen_languages.append(seen_params["lang"][0])
        return httpx.Response(200, json={"result": "[]"})

    provider = HkexNewsProvider(timeout_s=1)
    await provider._client.aclose()
    provider._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        result = await provider.fetch(
            NewsQuery(
                market="hk",
                symbol="0700.HK",
                language="en",
                since="2026-07-29T17:00:00Z",
                as_of="2026-07-29T18:00:00Z",
            )
        )
    finally:
        await provider.close()

    assert result.status == "no_results"
    assert seen_languages == ["en"]
    assert seen_params["fromDate"] == ["20260730"]
    assert seen_params["toDate"] == ["20260730"]


async def test_hkex_preserves_success_when_one_language_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """双语查询一端超时时仍保留另一端成功公告。"""
    provider = HkexNewsProvider(timeout_s=1)

    async def resolve(symbol: str) -> str:
        return "42"

    async def search(language: str, stock_id: str, query: NewsQuery):
        if language == "en":
            raise httpx.TimeoutException("timeout")
        return [{"NEWS_ID": "1", "TITLE": "全年業績", "DATE_TIME": "28/07/2026 18:30",
                 "FILE_LINK": "/listedco/listconews/sehk/2026/1c.pdf", "_language": "zh-HK"}]

    monkeypatch.setattr(provider, "_resolve_stock_id", resolve)
    monkeypatch.setattr(provider, "_search", search)
    try:
        result = await provider.fetch(NewsQuery(market="hk", symbol="0700.HK"))
    finally:
        await provider.close()
    assert result.status == "timeout"
    assert [item.title for item in result.items] == ["全年業績"]


async def test_sec_resolves_share_class_before_market_suffix() -> None:
    """SEC 应先识别 BRK.B 份额类别，再兼容 AAPL.US 市场后缀。"""
    provider = SecNewsProvider(user_agent="test test@example.com", timeout_s=1, min_interval_s=0)
    provider._ticker_map = {"BRK-B": "0001067983", "BRK": "0000000001", "AAPL": "0000320193"}
    try:
        assert await provider._resolve_cik("BRK.B") == "0001067983"
        assert await provider._resolve_cik("AAPL.US") == "0000320193"
    finally:
        await provider.close()


@pytest.mark.parametrize(
    ("provider", "query"),
    [
        (YahooNewsProvider(), NewsQuery(market="us", symbol="AAPL")),
        (YahooMarketNewsProvider(), NewsQuery(market="us")),
    ],
)
async def test_yahoo_providers_classify_rate_limit(
    monkeypatch: pytest.MonkeyPatch,
    provider: YahooNewsProvider | YahooMarketNewsProvider,
    query: NewsQuery,
) -> None:
    """Yahoo 限流需保留机器可读状态，不能退化成普通上游错误。"""
    class FakeConnector:
        async def fetch_news(self, symbol: str, limit: int = 20) -> list[dict[str, object]]:
            raise RuntimeError("Yahoo Finance rate limit exceeded")

    monkeypatch.setattr(
        "inalpha_data.connectors.news.legacy.yfinance_conn.get_connector",
        lambda: FakeConnector(),
    )
    result = await provider.fetch(query)
    assert result.status == "rate_limited"


async def test_rss_provider_reuses_cached_items_on_304() -> None:
    feed = FeedDefinition("test", "Test Feed", "https://feed.test/rss", "professional_media", "en")
    provider = RssFeedProvider(feed, timeout_s=1)
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(
                200,
                headers={"etag": '"v1"'},
                content=b"<rss version='2.0'><channel><item><guid>x</guid><title>T</title>"
                b"<link>https://x.test</link><pubDate>Tue, 28 Jul 2026 10:00:00 GMT</pubDate>"
                b"</item></channel></rss>",
            )
        assert request.headers["if-none-match"] == '"v1"'
        return httpx.Response(304)

    await provider._client.aclose()
    provider._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        first = await provider.fetch(NewsQuery(market="crypto", limit=5))
        second = await provider.fetch(NewsQuery(market="crypto", limit=5))
    finally:
        await provider.close()
    assert first.status == second.status == "ok"
    assert first.items[0].source_name == "test"
    assert second.items[0].title == "T"


def test_dedupe_prefers_url_across_provider_source_ids() -> None:
    """同一 canonical URL 跨 provider 应合并，即使各自 source_id 不同。"""
    query = NewsQuery(market="us", symbol="AAPL")
    ts = datetime(2026, 7, 28, tzinfo=UTC)
    wire = NewsItem(
        title="Event",
        link="https://x.test/a?utm_source=wire",
        published_at=ts,
        source_id="wire-1",
        source_name="wire",
        source_tier="professional_media",
    )
    aggregator = NewsItem(
        title="Event copy",
        link="https://x.test/a",
        published_at=ts,
        source_id="agg-9",
        source_name="aggregator",
        source_tier="aggregator",
    )

    result = filter_and_dedupe([aggregator, wire], query)

    assert len(result) == 1
    assert result[0].source_name == "wire"
    assert "aggregator" in result[0].alternative_sources


def test_realtime_query_rejects_future_items() -> None:
    """实时查询也应以抓取时点为上界。"""
    fetched_at = datetime(2026, 7, 28, 12, tzinfo=UTC)
    items = [
        NewsItem(title="visible", published_at=fetched_at, source_name="wire",
                 source_tier="professional_media"),
        NewsItem(title="future", published_at=datetime(2026, 7, 29, tzinfo=UTC),
                 source_name="wire", source_tier="professional_media"),
    ]
    result = filter_and_dedupe(
        items, NewsQuery(market="us", symbol="AAPL"), fetched_at=fetched_at
    )
    assert [item.title for item in result] == ["visible"]


def test_dedupe_prefers_official_source_without_mutating_inputs() -> None:
    query = NewsQuery(market="us", symbol="AAPL")
    ts = datetime(2026, 7, 28, tzinfo=UTC)
    media = NewsItem(title="Event", link="https://x.test/a?utm_source=z", published_at=ts,
                     source_name="wire", source_tier="professional_media")
    official = NewsItem(title="Event", link="https://x.test/a", published_at=ts,
                        source_name="sec", source_tier="official", kind="disclosure")
    result = filter_and_dedupe([media, official], query)
    assert len(result) == 1
    assert result[0].source_name == "sec"
    assert "wire" in result[0].alternative_sources
    assert media.alternative_sources == []
    assert official.alternative_sources == []


async def test_archive_retains_slow_source_when_newer_source_fills_global_limit() -> None:
    """归档按来源限制；正常新闻 API 仍按全局限制，且拒绝未来条目。"""
    class Provider:
        coverage = "snapshot_only"

        def __init__(self, name: str, titles: list[str], timestamp: datetime) -> None:
            self.name = name
            self.titles = titles
            self.timestamp = timestamp

        def supports(self, query: NewsQuery) -> bool:
            return True

        async def fetch(self, query: NewsQuery) -> ProviderResult:
            return ProviderResult(self.name, "ok", items=[
                NewsItem(title=title, source_name=self.name, published_at=self.timestamp)
                for title in self.titles
            ])

    router = NewsRouter([
        Provider("rss:busy", ["new-1", "new-2"], datetime(2026, 7, 29, tzinfo=UTC)),
        Provider("rss:official", ["older"], datetime(2026, 7, 28, tzinfo=UTC)),
        Provider("rss:future", ["future"], datetime(2099, 7, 28, tzinfo=UTC)),
    ])
    query = NewsQuery(market="crypto", limit=2)
    public = await router.fetch(query)
    archived = await router.fetch_archive_batches(query)
    assert [item.title for item in public.items] == ["new-1", "new-2"]
    assert [[item.title for item in batch.items] for batch in archived] == [
        ["new-1", "new-2"], ["older"], [],
    ]


async def test_archive_timeout_preserves_healthy_source_and_cached_observation() -> None:
    """来源超时不丢健康条目；缓存观测时间不能更新成当前首次可见时间。"""
    observed = datetime(2026, 7, 28, tzinfo=UTC)

    class Provider:
        coverage = "snapshot_only"

        def __init__(self, name: str, *, slow: bool = False) -> None:
            self.name = name
            self.slow = slow

        def supports(self, query: NewsQuery) -> bool:
            return True

        async def fetch(self, query: NewsQuery) -> ProviderResult:
            if self.slow:
                await asyncio.sleep(1)
            return ProviderResult(self.name, "ok", items=[
                NewsItem(title="visible", published_at=observed, fetched_at=observed,
                         source_name=self.name),
            ])

    router = NewsRouter([
        Provider("rss:healthy"), Provider("rss:slow", slow=True),
    ], timeout_s=0.01)
    batches = await router.fetch_archive_batches(NewsQuery(market="crypto"))
    assert batches[0].items[0].fetched_at == observed
    assert batches[0].is_partial is False
    assert batches[1].is_partial is True
    assert batches[1].providers[0].status == "timeout"
    assert batches[1].items == []


async def test_new_feeds_are_archive_only_and_public_scope_is_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """新增来源进入归档，但不改变公共列表、覆盖判断及标的能力边界。"""
    from unittest.mock import AsyncMock

    from inalpha_data.connectors.news.feed_models import DEFAULT_CRYPTO_FEEDS

    providers = [RssFeedProvider(feed, timeout_s=1) for feed in DEFAULT_CRYPTO_FEEDS]
    for provider in providers:
        monkeypatch.setattr(provider, "fetch", AsyncMock(return_value=ProviderResult(
            provider.name, "ok", items=[NewsItem(
                title=provider.definition.id, source_name=provider.definition.id,
                published_at=datetime(2026, 7, 28, tzinfo=UTC),
            )],
        )))
    try:
        router = NewsRouter(providers)
        query = NewsQuery(market="crypto", limit=50)
        public = await router.fetch(query)
        assert {item.source_name for item in public.items} == {"coindesk", "kraken_blog"}
        assert {batch.items[0].source_name for batch in await router.fetch_archive_batches(query)} == {
            "coindesk", "kraken_blog", "bitcoin_core_announcements", "cointelegraph",
        }
        archive_router = NewsRouter([p for p in providers if p.archive_only])
        assert archive_router.has_coverage(query) is False
        assert (await archive_router.fetch(query)).items == []
        assert await router.fetch_archive_batches(NewsQuery(market="crypto", symbol="BTC")) == []
    finally:
        await router.close()
