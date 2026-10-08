"""RSS/Atom feed 定义与条目转换。"""
from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from ...news_models import NewsItem, NewsQuery, SourceTier


@dataclass(frozen=True, slots=True)
class FeedDefinition:
    """显式声明 feed 的身份与覆盖边界。"""

    id: str
    name: str
    url: str
    tier: SourceTier
    language: str
    archive_only: bool = False


DEFAULT_CRYPTO_FEEDS = (
    FeedDefinition(
        "coindesk", "CoinDesk", "https://www.coindesk.com/arc/outboundfeeds/rss/",
        "professional_media", "en"
    ),
    FeedDefinition(
        "kraken_blog", "Kraken Blog", "https://blog.kraken.com/feed", "official", "en"
    ),
    FeedDefinition(
        "bitcoin_core_announcements", "Bitcoin Core", "https://bitcoincore.org/en/announcements.xml",
        "official", "en", archive_only=True
    ),
    FeedDefinition(
        "cointelegraph", "Cointelegraph", "https://cointelegraph.com/rss",
        "professional_media", "en", archive_only=True
    ),
)


def parse_entry(
    value: dict[str, Any], definition: FeedDefinition, query: NewsQuery, fetched_at: datetime
) -> NewsItem:
    """把 feedparser entry 转为统一新闻条目。"""
    return NewsItem(
        title=str(value.get("title") or ""),
        publisher=definition.name,
        link=str(value.get("link") or ""),
        published_at=_entry_time(value),
        summary=str(value.get("summary") or "")[:500],
        kind="media",
        source_id=str(value.get("id") or value.get("guid") or ""),
        source_name=definition.id,
        source_tier=definition.tier,
        fetched_at=fetched_at,
        market="crypto",
        language=definition.language,
    )


def _entry_time(value: dict[str, Any]) -> datetime | None:
    parsed = value.get("published_parsed") or value.get("updated_parsed")
    if parsed:
        try:
            return datetime.fromtimestamp(calendar.timegm(parsed), tz=UTC)
        except (TypeError, ValueError, OverflowError):
            return None
    return None
