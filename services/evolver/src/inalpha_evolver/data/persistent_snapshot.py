"""Canonical immutable persistence for E2 market datasets."""

from __future__ import annotations

import gzip
import hashlib
import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from inalpha_paper.kernel.identifiers import InstrumentId
from inalpha_paper.market_evaluation import build_market_evaluation_context
from inalpha_paper.model.data import Bar
from psycopg import AsyncConnection

from .bar_hash import bars_content_hash
from .manifest import DatasetManifest, FrozenDataset

CANONICAL_VERSION = "e2-bars-snapshot-v1"


def encode_frozen_dataset(dataset: FrozenDataset) -> tuple[bytes, str]:
    """Return deterministic gzip bytes and the hash of canonical JSON."""
    payload = {
        "canonical_version": CANONICAL_VERSION,
        "manifest": dataset.manifest.model_dump(mode="json"),
        "bars": [
            {
                "instrument": {
                    "symbol": bar.instrument_id.symbol,
                    "venue": bar.instrument_id.venue,
                },
                "timeframe": bar.timeframe,
                "open": bar.open,
                "high": bar.high,
                "low": bar.low,
                "close": bar.close,
                "volume": bar.volume,
                "ts_event": bar.ts_event,
                "ts_init": bar.ts_init,
                "data_epoch": bar.data_epoch,
                "is_stale_after_reconnect": bar.is_stale_after_reconnect,
                "ts_open": bar.ts_open,
            }
            for bar in dataset.bars
        ],
    }
    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return gzip.compress(canonical, mtime=0), hashlib.sha256(canonical).hexdigest()


def decode_frozen_dataset(row: dict[str, Any]) -> FrozenDataset:
    """Validate and reconstruct a snapshot; corruption always fails closed."""
    compressed = bytes(row["compressed_payload"])
    canonical = gzip.decompress(compressed)
    actual_hash = hashlib.sha256(canonical).hexdigest()
    if actual_hash != row["content_sha256"]:
        raise RuntimeError("evolution data snapshot hash mismatch")
    payload = json.loads(canonical)
    if payload.get("canonical_version") != CANONICAL_VERSION:
        raise RuntimeError("unsupported evolution data snapshot version")
    bars = tuple(
        Bar(
            instrument_id=InstrumentId(
                symbol=item["instrument"]["symbol"],
                venue=item["instrument"]["venue"],
            ),
            timeframe=item["timeframe"],
            open=float(item["open"]),
            high=float(item["high"]),
            low=float(item["low"]),
            close=float(item["close"]),
            volume=float(item["volume"]),
            ts_event=int(item["ts_event"]),
            ts_init=int(item["ts_init"]),
            data_epoch=int(item["data_epoch"]),
            is_stale_after_reconnect=bool(item["is_stale_after_reconnect"]),
            ts_open=int(item["ts_open"]) if item["ts_open"] is not None else None,
        )
        for item in payload["bars"]
    )
    if len(bars) != int(row["bar_count"]):
        raise RuntimeError("evolution data snapshot bar count mismatch")
    expected_discovery = max(2, int(len(bars) * 0.60))
    expected_validation = min(
        max(expected_discovery + 2, int(len(bars) * 0.80)),
        len(bars) - 1,
    )
    if (
        int(row["discovery_end"]) != expected_discovery
        or int(row["validation_end"]) != expected_validation
    ):
        raise RuntimeError("evolution data snapshot split boundary mismatch")
    manifest = DatasetManifest.model_validate(payload["manifest"])
    if (
        manifest.venue != row["venue"]
        or manifest.symbol != row["symbol"]
        or manifest.requested_timeframe != row["timeframe"]
    ):
        raise RuntimeError("evolution data snapshot market identity mismatch")
    return FrozenDataset(bars=bars, manifest=manifest)


async def get_campaign_data_snapshot(
    conn: AsyncConnection,
    campaign_id: UUID,
) -> dict[str, Any] | None:
    """Load one immutable campaign dataset row."""
    async with conn.cursor() as cur:
        await cur.execute(
            """SELECT s.* FROM evolution_campaigns c
JOIN evolution_data_snapshots s ON s.owner_account_id=c.owner_account_id
AND (s.campaign_id=c.campaign_id OR s.loop_id IN (
  SELECT l.loop_id FROM evolution_loops l
  WHERE l.campaign_id=c.campaign_id AND l.owner_account_id=c.owner_account_id
)) WHERE c.campaign_id=%s""",
            (campaign_id,),
        )
        row = await cur.fetchone()
    return dict(row) if row is not None else None


async def get_loop_data_snapshot(
    conn: AsyncConnection, loop_id: UUID, owner_account_id: UUID,
) -> dict[str, Any] | None:
    """Load the owner-bound dataset frozen before any baseline evaluation."""
    cursor = await conn.execute(
        "SELECT * FROM evolution_data_snapshots WHERE loop_id=%s AND owner_account_id=%s",
        (loop_id, owner_account_id),
    )
    row = await cursor.fetchone()
    return dict(row) if row else None


async def persist_loop_data_snapshot(
    conn: AsyncConnection, *, loop_id: UUID, owner_account_id: UUID,
    lease_token: UUID, dataset: FrozenDataset,
) -> dict[str, Any]:
    """Freeze once under a live loop lease; repeat writes retain the first payload."""
    payload, digest = encode_frozen_dataset(dataset)
    count = len(dataset.bars)
    discovery_end = max(2, int(count * 0.60))
    validation_end = min(max(discovery_end + 2, int(count * 0.80)), count - 1)
    manifest = dataset.manifest
    async with conn.transaction():
        cursor = await conn.execute(
            """SELECT loop_id FROM evolution_loops WHERE loop_id=%s AND owner_account_id=%s
AND lease_token=%s AND lease_expires_at>=clock_timestamp()
AND status IN ('target_resolved','baseline_ready') AND campaign_id IS NULL FOR UPDATE""",
            (loop_id, owner_account_id, lease_token),
        )
        if await cursor.fetchone() is None:
            raise RuntimeError("loop lease fencing token was lost")
        await conn.execute(
            """INSERT INTO evolution_data_snapshots(
snapshot_id,loop_id,owner_account_id,venue,symbol,timeframe,from_ts,as_of,
canonical_version,content_sha256,compressed_payload,bar_count,discovery_end,validation_end)
SELECT %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s
FROM evolution_loops WHERE loop_id=%s AND lease_token=%s
AND lease_expires_at>=clock_timestamp()
ON CONFLICT(loop_id) DO NOTHING""",
            (uuid4(), loop_id, owner_account_id, manifest.venue, manifest.symbol,
             manifest.requested_timeframe, manifest.requested_from, manifest.requested_as_of,
             CANONICAL_VERSION, digest, payload, count, discovery_end, validation_end,
             loop_id, lease_token),
        )
        row = await get_loop_data_snapshot(conn, loop_id, owner_account_id)
        if row is None:
            raise RuntimeError("loop lease fencing token was lost")
    return row


async def persist_campaign_data_snapshot(
    conn: AsyncConnection,
    *,
    campaign_id: UUID,
    owner_account_id: UUID,
    lease_token: UUID,
    dataset: FrozenDataset,
) -> dict[str, Any]:
    """Reuse a loop's snapshot rather than creating a second campaign dataset."""
    async with conn.transaction():
        cursor = await conn.execute(
            """SELECT campaign_id FROM evolution_campaigns WHERE campaign_id=%s
AND owner_account_id=%s AND lease_token=%s AND lease_expires_at>=clock_timestamp()
FOR UPDATE""",
            (campaign_id, owner_account_id, lease_token),
        )
        if await cursor.fetchone() is None:
            raise RuntimeError("campaign lease fencing token was lost")
        existing = await get_campaign_data_snapshot(conn, campaign_id)
        if existing is not None:
            await conn.execute(
                """UPDATE evolution_campaigns SET data_snapshot_id=%s
WHERE campaign_id=%s AND data_snapshot_id IS NULL""",
                (existing["snapshot_id"], campaign_id),
            )
            return existing
        cursor = await conn.execute(
            """SELECT l.loop_id FROM evolution_loops l
JOIN evolution_loop_authorizations a USING(loop_id)
WHERE l.campaign_id=%s AND l.owner_account_id=%s""",
            (campaign_id, owner_account_id),
        )
        if await cursor.fetchone() is not None:
            raise RuntimeError("automatic campaign is missing its baseline data snapshot")
        return await _persist_campaign_data_snapshot(
            conn, campaign_id=campaign_id, owner_account_id=owner_account_id,
            lease_token=lease_token, dataset=dataset,
        )


async def _persist_campaign_data_snapshot(
    conn: AsyncConnection,
    *,
    campaign_id: UUID,
    owner_account_id: UUID,
    lease_token: UUID,
    dataset: FrozenDataset,
) -> dict[str, Any]:
    """Insert exactly once and return the winning immutable row."""
    payload, content_sha256 = encode_frozen_dataset(dataset)
    bar_count = len(dataset.bars)
    discovery_end = max(2, int(bar_count * 0.60))
    validation_end = min(
        max(discovery_end + 2, int(bar_count * 0.80)),
        bar_count - 1,
    )
    snapshot_id = uuid4()
    manifest = dataset.manifest
    async with conn.cursor() as cur:
        await cur.execute(
            """INSERT INTO evolution_data_snapshots(
snapshot_id,campaign_id,owner_account_id,venue,symbol,timeframe,from_ts,as_of,
canonical_version,content_sha256,compressed_payload,bar_count,discovery_end,validation_end)
SELECT %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s
FROM evolution_campaigns c WHERE c.campaign_id=%s AND c.owner_account_id=%s
AND c.lease_token=%s AND c.lease_expires_at>=NOW()
ON CONFLICT(campaign_id) DO NOTHING""",
            (
                snapshot_id,
                campaign_id,
                owner_account_id,
                manifest.venue,
                manifest.symbol,
                manifest.requested_timeframe,
                manifest.requested_from,
                manifest.requested_as_of,
                CANONICAL_VERSION,
                content_sha256,
                payload,
                bar_count,
                discovery_end,
                validation_end,
                campaign_id,
                owner_account_id,
                lease_token,
            ),
        )
        await cur.execute(
            """SELECT s.* FROM evolution_data_snapshots s
JOIN evolution_campaigns c USING(campaign_id)
WHERE s.campaign_id=%s AND c.owner_account_id=%s AND c.lease_token=%s
AND c.lease_expires_at>=NOW()""",
            (campaign_id, owner_account_id, lease_token),
        )
        row = await cur.fetchone()
        if row is None:
            raise RuntimeError("campaign lease fencing token was lost")
        await cur.execute(
            """UPDATE evolution_campaigns SET data_snapshot_id=%s,updated_at=NOW()
WHERE campaign_id=%s AND owner_account_id=%s AND lease_token=%s
AND lease_expires_at>=NOW() AND data_snapshot_id IS NULL""",
            (row["snapshot_id"], campaign_id, owner_account_id, lease_token),
        )
    return dict(row)


def snapshot_splits(row: dict[str, Any]) -> tuple[int, int]:
    """Return persisted discovery and validation endpoints."""
    return int(row["discovery_end"]), int(row["validation_end"])


def discovery_dataset(row: dict[str, Any]) -> FrozenDataset:
    """Expose only discovery bars and corresponding metadata to baseline research."""
    full = decode_frozen_dataset(row)
    bars = full.bars[:int(row["discovery_end"])]
    first = datetime.fromtimestamp(bars[0].bar_open_at / 1e9, tz=UTC)
    latest = datetime.fromtimestamp(bars[-1].bar_open_at / 1e9, tz=UTC)
    cutoff = datetime.fromtimestamp(bars[-1].bar_known_at / 1e9, tz=UTC)
    context = build_market_evaluation_context(
        venue=full.manifest.venue, symbol=full.manifest.symbol,
        timeframe=full.manifest.requested_timeframe, as_of=cutoff,
    )
    manifest = full.manifest.model_copy(update={
        "requested_as_of": cutoff,
        "effective_from": first,
        "effective_to": latest,
        "latest_bar_ts": latest,
        "cutoff_bar_ts": latest,
        "freshness_lag_seconds": 0,
        "data_epoch": int(latest.timestamp() * 1000),
        "bar_count": len(bars),
        "content_sha256": bars_content_hash(list(bars), bars[0].instrument_id, context),
        "backfill": full.manifest.backfill.model_copy(update={
            "bars_fetched": len(bars), "bars_inserted": len(bars),
            "from_ts": first, "to_ts": cutoff,
        }),
        "warnings": ["discovery-only view of the immutable loop snapshot"],
    })
    return FrozenDataset(bars=bars, manifest=manifest)


__all__ = [
    "CANONICAL_VERSION",
    "decode_frozen_dataset",
    "discovery_dataset",
    "encode_frozen_dataset",
    "get_campaign_data_snapshot",
    "get_loop_data_snapshot",
    "persist_campaign_data_snapshot",
    "persist_loop_data_snapshot",
    "snapshot_splits",
]
