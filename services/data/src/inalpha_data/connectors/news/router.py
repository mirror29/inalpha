"""多市场财经新闻聚合路由。"""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from ...news_models import NewsProviderStatus, NewsQuery, NewsResponse
from .base import NewsProvider, ProviderResult
from .dedupe import filter_and_dedupe
from .legacy import CnNewsProvider, YahooNewsProvider
from .market_proxy import YahooMarketNewsProvider


class NewsRouter:
    """选择 provider，并保留部分失败与覆盖状态。"""

    def __init__(self, providers: list[NewsProvider], *, timeout_s: float = 15.0) -> None:
        self._providers = providers
        self._timeout_s = timeout_s

    async def fetch(self, query: NewsQuery) -> NewsResponse:
        """并发调用 provider，聚合、PIT 过滤并去重。"""
        fetched_at = datetime.now(UTC)
        selected = self._select(query)
        results = await asyncio.gather(
            *(self._fetch_with_timeout(provider, query) for provider in selected)
        )
        for provider, result in zip(selected, results, strict=True):
            result.coverage = provider.coverage
        items = filter_and_dedupe(
            [item for result in results for item in result.items], query, fetched_at=fetched_at
        )
        statuses = [_status(result) for result in results]
        failures = {"timeout", "rate_limited", "upstream_error"}
        return NewsResponse(
            venue=query.venue,
            market=query.market,
            symbol=query.symbol,
            as_of=query.as_of,
            since=query.since,
            fetched_at=fetched_at,
            items=items,
            providers=statuses,
            is_partial=any(status.status in failures for status in statuses),
            coverage_complete=(
                not (query.as_of or query.since)
                or all(provider.coverage == "complete" for provider in selected)
            ),
        )

    async def _fetch_with_timeout(
        self, provider: NewsProvider, query: NewsQuery
    ) -> ProviderResult:
        """给每个 provider 独立截止时间，避免单一长尾阻塞聚合。"""
        try:
            return await asyncio.wait_for(provider.fetch(query), timeout=self._timeout_s)
        except TimeoutError:
            return ProviderResult(
                provider.name,
                "timeout",
                error=f"provider timed out after {self._timeout_s:g}s",
                coverage=provider.coverage,
            )

    async def fetch_archive_batches(self, query: NewsQuery) -> list[NewsResponse]:
        """为持续归档保留每个来源的有界批次，避免全局排序挤掉较慢来源。"""
        fetched_at = datetime.now(UTC)
        selected = self._select(query)
        results = await asyncio.gather(
            *(self._fetch_with_timeout(provider, query) for provider in selected)
        )
        batches = []
        for provider, result in zip(selected, results, strict=True):
            result.coverage = provider.coverage
            status = _status(result)
            batches.append(NewsResponse(
                venue=query.venue,
                market=query.market,
                symbol=query.symbol,
                as_of=query.as_of,
                since=query.since,
                fetched_at=fetched_at,
                items=filter_and_dedupe(result.items, query, fetched_at=fetched_at),
                providers=[status],
                is_partial=status.status in {"timeout", "rate_limited", "upstream_error"},
                coverage_complete=(
                    not (query.as_of or query.since) or provider.coverage == "complete"
                ),
            ))
        return batches

    def _select(self, query: NewsQuery) -> list[NewsProvider]:
        """按市场初选来源，再按 provider capability 排除无覆盖组合。"""
        names: set[str]
        if query.market == "cn":
            names = {"eastmoney"}
        elif query.market in {"us", "hk"} and query.symbol:
            wants_disclosures = not query.kinds or "disclosure" in query.kinds
            names = {"yfinance", *({query.market} if wants_disclosures else set())}
        elif query.symbol and query.market in {
            "jp", "kr", "au", "in", "uk", "de", "fr", "ca", "br", "global"
        }:
            names = {"yfinance"}
        elif query.market in {"us", "hk", "jp", "kr", "au", "in", "uk", "de", "fr", "ca", "br", "global"}:
            names = {"yfinance_market_proxy"}
        elif query.market == "crypto":
            names = {"rss"}
        elif query.venue in {"baostock", "akshare"}:
            names = {"eastmoney"}
        elif query.venue == "yfinance" or query.symbol:
            names = {"yfinance"}
        else:
            names = set()
        return [
            provider
            for provider in self._providers
            if (
                provider.name in names
                or ("rss" in names and provider.name.startswith("rss:"))
            )
            and provider.supports(query)
        ]

    def has_coverage(self, query: NewsQuery) -> bool:
        """是否至少有一个 provider 覆盖当前查询。"""
        return bool(self._select(query))

    async def close(self) -> None:
        """关闭自有 provider 资源。"""
        await asyncio.gather(*(provider.close() for provider in self._providers))


_router: NewsRouter | None = None


def init_router(
    extra_providers: list[NewsProvider] | None = None, *, timeout_s: float = 15.0
) -> None:
    """初始化模块级 router。"""
    global _router
    if _router is not None:
        raise RuntimeError("news router already initialized")
    _router = NewsRouter(
        [CnNewsProvider(), YahooNewsProvider(), YahooMarketNewsProvider(), *(extra_providers or [])],
        timeout_s=timeout_s,
    )


async def close_router() -> None:
    """关闭并清除模块级 router。"""
    global _router
    if _router is not None:
        await _router.close()
        _router = None


def get_router() -> NewsRouter:
    """返回已初始化 router。"""
    if _router is None:
        raise RuntimeError("news router not initialized")
    return _router


def _status(result: ProviderResult) -> NewsProviderStatus:
    """转换 provider 状态模型。"""
    return NewsProviderStatus(
        provider=result.provider,
        status=result.status,
        error=result.error,
        fetched_at=result.fetched_at,
        item_count=len(result.items),
        coverage=result.coverage,
    )
