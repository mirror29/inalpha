"""统一新闻去重与 point-in-time 过滤。"""
from __future__ import annotations

import re
from datetime import UTC, datetime
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from ...news_models import NewsItem, NewsQuery

_TRACKING_KEYS = {"gclid", "fbclid", "ref", "source"}
_TIER_WEIGHT = {"official": 3, "professional_media": 2, "aggregator": 1}


def filter_and_dedupe(
    items: list[NewsItem], query: NewsQuery, *, fetched_at: datetime | None = None,
    apply_limit: bool = True,
) -> list[NewsItem]:
    """按时间窗、类型过滤并跨 provider 去重。"""
    filtered = [item for item in items if _visible(item, query, fetched_at)]
    winners: dict[str, NewsItem] = {}
    for item in filtered:
        key = _event_key(item)
        current = winners.get(key)
        if current is None:
            winners[key] = item
            continue
        if _TIER_WEIGHT[item.source_tier] > _TIER_WEIGHT[current.source_tier]:
            winners[key] = item.model_copy(
                deep=True,
                update={"alternative_sources": _sources(current, item)},
            )
        else:
            winners[key] = current.model_copy(
                deep=True,
                update={"alternative_sources": _sources(current, item)},
            )
    epoch = datetime.min.replace(tzinfo=UTC)
    ranked = sorted(winners.values(), key=lambda item: item.published_at or epoch, reverse=True)
    return ranked[: query.limit] if apply_limit else ranked


def canonical_url(url: str) -> str:
    """移除常见追踪参数并稳定化 URL。"""
    if not url:
        return ""
    parts = urlsplit(url)
    query = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if not key.lower().startswith("utm_") and key.lower() not in _TRACKING_KEYS
    ]
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path, urlencode(query), ""))


def _visible(item: NewsItem, query: NewsQuery, fetched_at: datetime | None) -> bool:
    if query.kinds and item.kind not in query.kinds:
        return False
    upper_bound = query.as_of or fetched_at
    if upper_bound and (item.published_at is None or item.published_at > upper_bound):
        return False
    if query.since and (item.published_at is None or item.published_at < query.since):
        return False
    return True


def _event_key(item: NewsItem) -> str:
    url = canonical_url(item.link)
    if url:
        return f"url:{url}"
    if item.source_id:
        return f"id:{item.source_name}:{item.source_id}"
    title = re.sub(r"\W+", "", item.title.casefold())
    bucket = item.published_at.strftime("%Y%m%d%H") if item.published_at else "unknown"
    return f"title:{title}:{bucket}"


def _sources(left: NewsItem, right: NewsItem) -> list[str]:
    values = [*left.alternative_sources, *right.alternative_sources]
    values.extend(value for value in (left.source_name, right.source_name) if value)
    return sorted(set(values))
