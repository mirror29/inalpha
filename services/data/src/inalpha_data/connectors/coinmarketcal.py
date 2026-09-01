"""CoinMarketCal v2 historical event connector.

Only documented v2 fields are consumed. Provider payloads are retained in the raw
ledger so later extractor upgrades can reproduce normalized facts.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from ..event_models import CoinMarketCalImportRequest, RawEventIngestRequest


class CoinMarketCalConnector:
    """Fetch bounded pages from the configured Professional event catalog."""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        timeout_s: float,
        historical_latency_s: int,
    ) -> None:
        self._api_key = api_key
        self._historical_latency = timedelta(seconds=historical_latency_s)
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=timeout_s,
            trust_env=False,
            headers={"x-api-key": api_key, "User-Agent": "Inalpha/0.3 event-research"},
        )

    @property
    def configured(self) -> bool:
        return bool(self._api_key)

    async def fetch(self, request: CoinMarketCalImportRequest) -> list[RawEventIngestRequest]:
        """Fetch at most ``request.limit`` records across cursor pages."""
        if not self.configured:
            raise RuntimeError("COINMARKETCAL_API_KEY is not configured")
        records: list[RawEventIngestRequest] = []
        cursor: str | None = None
        while len(records) < request.limit:
            page_limit = min(100, request.limit - len(records))
            params: dict[str, Any] = {
                "from": request.from_date.date().isoformat(),
                "to": request.to_date.date().isoformat(),
                "limit": page_limit,
            }
            if request.coins:
                params["coins"] = ",".join(request.coins)
            if request.categories:
                params["categories"] = ",".join(request.categories)
            if cursor:
                params["cursor"] = cursor
            response = await self._client.get("/v2/events", params=params)
            response.raise_for_status()
            payload = response.json()
            data = payload.get("data") if isinstance(payload, dict) else None
            if not isinstance(data, list):
                raise ValueError("CoinMarketCal response data must be a list")
            fetched_at = datetime.now(UTC)
            records.extend(
                self._convert(item, fetched_at) for item in data if isinstance(item, dict)
            )
            meta = payload.get("meta") if isinstance(payload, dict) else None
            cursor = (
                str(meta.get("cursor")) if isinstance(meta, dict) and meta.get("cursor") else None
            )
            if not cursor or not data:
                break
        return records[: request.limit]

    def _convert(self, item: dict[str, Any], fetched_at: datetime) -> RawEventIngestRequest:
        source_event_id = str(item.get("id") or item.get("slug") or "").strip()
        if not source_event_id:
            raise ValueError("CoinMarketCal event is missing id")
        event_at = _parse_time(item.get("date"))
        added_at = _parse_time(
            item.get("dateAdded") or item.get("createdAt") or item.get("created_at")
        )
        # Historical catalog rows use provider first-add time plus a frozen safety latency.
        # If the provider omits it, the record is only known at this import's first_seen time.
        first_seen_at = added_at + self._historical_latency if added_at else fetched_at
        proof = item.get("proof") or item.get("source") or item.get("originalSource")
        url = proof if isinstance(proof, str) else None
        return RawEventIngestRequest(
            source="coinmarketcal",
            source_event_id=source_event_id,
            title=str(item.get("title") or ""),
            content=str(item.get("description") or ""),
            url=url,
            raw_payload=item,
            source_valid_at=event_at,
            claimed_published_at=added_at,
            first_seen_at=first_seen_at,
            fetched_at=fetched_at,
            accepted_at=max(first_seen_at, fetched_at) if not added_at else first_seen_at,
            collector_version="coinmarketcal-v2@1",
            policy_version="structured-provider-latency-v1",
            source_tier="structured",
            retracted=bool(item.get("cancelled") or item.get("isCancelled")),
        )

    async def close(self) -> None:
        """Close the underlying HTTP connection pool."""
        await self._client.aclose()


_connector: CoinMarketCalConnector | None = None


def init_connector(
    *, api_key: str, base_url: str, timeout_s: float, historical_latency_s: int
) -> None:
    """Initialize the process-wide connector during FastAPI lifespan startup."""
    global _connector
    if _connector is not None:
        raise RuntimeError("CoinMarketCal connector already initialized")
    _connector = CoinMarketCalConnector(
        api_key=api_key,
        base_url=base_url,
        timeout_s=timeout_s,
        historical_latency_s=historical_latency_s,
    )


def get_connector() -> CoinMarketCalConnector:
    """Return the initialized process-wide connector."""
    if _connector is None:
        raise RuntimeError("CoinMarketCal connector not initialized")
    return _connector


async def close_connector() -> None:
    """Close and clear the process-wide connector."""
    global _connector
    if _connector is not None:
        await _connector.close()
        _connector = None


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


__all__ = ["CoinMarketCalConnector", "close_connector", "get_connector", "init_connector"]
