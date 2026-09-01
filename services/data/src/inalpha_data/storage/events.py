"""Immutable market-event ledger, fact versions, and frozen snapshots."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from psycopg import AsyncConnection

from ..event_models import EventFactWriteRequest, EventSnapshotRequest, RawEventIngestRequest

_RAW_COLUMNS = """event_id,source,source_event_id,version,title,content,url,content_hash,
raw_payload,source_valid_at,claimed_published_at,first_seen_at,fetched_at,accepted_at,
collector_version,policy_version,source_tier,supersedes_event_id,retracted,created_at"""
_FACT_COLUMNS = """fact_id,raw_event_id,fact_key,version,fact_hash,event_type,assets,actor,
action,severity,confidence,effective_at,available_at,evidence_spans,extractor_version,
policy_version,supersedes_fact_id,retracted,created_at"""
_SNAPSHOT_COLUMNS = """snapshot_id,cutoff,policy_version,query_hash,events_sha256,coverage,
event_types,assets,fact_count,created_at"""


def _canonical_hash(value: Any) -> str:
    """Hash JSON with stable ordering and compact separators."""
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=_json_default,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _json_default(value: Any) -> str:
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
    if isinstance(value, UUID):
        return str(value)
    raise TypeError(f"unsupported canonical JSON value: {type(value).__name__}")


def _advisory_lock_key(*parts: object) -> str:
    """Return a PostgreSQL-safe, collision-resistant text key for advisory locks."""
    return _canonical_hash(list(parts))


async def ingest_raw_event(
    conn: AsyncConnection,
    request: RawEventIngestRequest,
) -> tuple[dict[str, Any], bool]:
    """Append an event revision while treating an identical payload as an idempotent retry."""
    payload = request.model_dump(mode="json")
    content_hash = _canonical_hash(
        {
            "title": request.title,
            "content": request.content,
            "url": request.url,
            "raw_payload": request.raw_payload,
            "retracted": request.retracted,
        }
    )
    lock_key = _advisory_lock_key(request.source, request.source_event_id)
    async with conn.cursor() as cur:
        await cur.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (lock_key,))
        await cur.execute(
            f"""SELECT {_RAW_COLUMNS} FROM raw_market_events
WHERE source=%s AND source_event_id=%s ORDER BY version DESC LIMIT 1""",
            (request.source, request.source_event_id),
        )
        latest = await cur.fetchone()
        if latest is not None and latest["content_hash"] == content_hash:
            return dict(latest), False
        version = int(latest["version"]) + 1 if latest is not None else 1
        event_id = uuid4()
        await cur.execute(
            f"""INSERT INTO raw_market_events(
event_id,source,source_event_id,version,title,content,url,content_hash,raw_payload,
source_valid_at,claimed_published_at,first_seen_at,fetched_at,accepted_at,
collector_version,policy_version,source_tier,supersedes_event_id,retracted)
VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
RETURNING {_RAW_COLUMNS}""",
            (
                event_id,
                request.source,
                request.source_event_id,
                version,
                request.title,
                request.content,
                request.url,
                content_hash,
                json.dumps(payload["raw_payload"], ensure_ascii=False),
                request.source_valid_at,
                request.claimed_published_at,
                request.first_seen_at,
                request.fetched_at,
                request.accepted_at,
                request.collector_version,
                request.policy_version,
                request.source_tier,
                latest["event_id"] if latest is not None else None,
                request.retracted,
            ),
        )
        row = await cur.fetchone()
    assert row is not None
    return dict(row), True


async def write_fact(
    conn: AsyncConnection,
    request: EventFactWriteRequest,
) -> tuple[dict[str, Any], bool]:
    """Append a normalized fact revision after validating its raw event boundary."""
    fact_payload = request.model_dump(mode="json", exclude={"raw_event_id"})
    fact_hash = _canonical_hash(fact_payload)
    lock_key = _advisory_lock_key(request.raw_event_id, request.fact_key)
    async with conn.cursor() as cur:
        await cur.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (lock_key,))
        await cur.execute(
            """SELECT first_seen_at,accepted_at,source_tier,policy_version
FROM raw_market_events WHERE event_id=%s""",
            (request.raw_event_id,),
        )
        raw = await cur.fetchone()
        if raw is None:
            raise LookupError("raw event not found")
        # Real-time sources may never be backdated. Structured historical providers
        # may use their frozen first-added policy, but still cannot predate it.
        earliest_available = (
            raw["first_seen_at"] if raw["source_tier"] == "structured" else raw["accepted_at"]
        )
        if request.available_at < earliest_available:
            raise ValueError(
                "available_at predates the source's point-in-time availability boundary"
            )
        await cur.execute(
            f"""SELECT {_FACT_COLUMNS} FROM market_event_facts
WHERE raw_event_id=%s AND fact_key=%s ORDER BY version DESC LIMIT 1""",
            (request.raw_event_id, request.fact_key),
        )
        latest = await cur.fetchone()
        if latest is not None and latest["fact_hash"] == fact_hash:
            return dict(latest), False
        version = int(latest["version"]) + 1 if latest is not None else 1
        fact_id = uuid4()
        await cur.execute(
            f"""INSERT INTO market_event_facts(
fact_id,raw_event_id,fact_key,version,fact_hash,event_type,assets,actor,action,
severity,confidence,effective_at,available_at,evidence_spans,extractor_version,
policy_version,supersedes_fact_id,retracted)
VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s)
RETURNING {_FACT_COLUMNS}""",
            (
                fact_id,
                request.raw_event_id,
                request.fact_key,
                version,
                fact_hash,
                request.event_type,
                request.assets,
                request.actor,
                request.action,
                request.severity,
                request.confidence,
                request.effective_at,
                request.available_at,
                json.dumps([item.model_dump(mode="json") for item in request.evidence_spans]),
                request.extractor_version,
                request.policy_version,
                latest["fact_id"] if latest is not None else None,
                request.retracted,
            ),
        )
        row = await cur.fetchone()
    assert row is not None
    return dict(row), True


async def create_snapshot(
    conn: AsyncConnection,
    request: EventSnapshotRequest,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Freeze latest visible fact versions using only their point-in-time availability."""
    query_payload = request.model_dump(mode="json")
    query_hash = _canonical_hash(query_payload)
    conditions = [
        "f.available_at<=%s",
        "r.accepted_at<=%s",
        "f.policy_version=%s",
    ]
    params: list[Any] = [request.cutoff, request.cutoff, request.policy_version]
    if request.event_types:
        conditions.append("f.event_type=ANY(%s)")
        params.append(request.event_types)
    if request.assets:
        conditions.append("f.assets&&%s::text[]")
        params.append(request.assets)
    where = " AND ".join(conditions)
    async with conn.cursor() as cur:
        await cur.execute(
            f"""SELECT DISTINCT ON (r.source,r.source_event_id,f.fact_key)
{",".join(f"f.{name.strip()}" for name in _FACT_COLUMNS.split(","))}
FROM market_event_facts f JOIN raw_market_events r ON r.event_id=f.raw_event_id
WHERE {where}
ORDER BY r.source,r.source_event_id,f.fact_key,f.available_at DESC,f.version DESC,f.fact_id""",
            params,
        )
        visible = [dict(row) for row in await cur.fetchall()]
        facts = [row for row in visible if not row["retracted"]]
        facts.sort(key=lambda row: (row["available_at"], str(row["fact_id"])))
        events_sha256 = _canonical_hash(
            [{"fact_id": str(row["fact_id"]), "fact_hash": row["fact_hash"]} for row in facts]
        )
        await cur.execute(
            """SELECT source,count(*) AS raw_count,max(accepted_at) AS latest_accepted_at
FROM raw_market_events WHERE accepted_at<=%s GROUP BY source ORDER BY source""",
            (request.cutoff,),
        )
        coverage_rows = [dict(row) for row in await cur.fetchall()]
        coverage = {"sources": coverage_rows, "complete": bool(coverage_rows)}
        await cur.execute(
            f"""INSERT INTO market_event_snapshots(
snapshot_id,cutoff,policy_version,query_hash,events_sha256,coverage,event_types,assets,fact_count)
VALUES(%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s)
ON CONFLICT(query_hash,events_sha256) DO UPDATE SET query_hash=EXCLUDED.query_hash
RETURNING {_SNAPSHOT_COLUMNS}""",
            (
                uuid4(),
                request.cutoff,
                request.policy_version,
                query_hash,
                events_sha256,
                json.dumps(coverage, default=_json_default),
                request.event_types,
                request.assets,
                len(facts),
            ),
        )
        snapshot = await cur.fetchone()
        assert snapshot is not None
        for ordinal, fact in enumerate(facts):
            await cur.execute(
                """INSERT INTO market_event_snapshot_facts(snapshot_id,fact_id,ordinal)
VALUES(%s,%s,%s) ON CONFLICT(snapshot_id,fact_id) DO NOTHING""",
                (snapshot["snapshot_id"], fact["fact_id"], ordinal),
            )
    return dict(snapshot), facts


async def get_snapshot(
    conn: AsyncConnection,
    snapshot_id: UUID,
) -> tuple[dict[str, Any], list[dict[str, Any]]] | None:
    """Load an immutable snapshot and its stable fact ordering."""
    async with conn.cursor() as cur:
        await cur.execute(
            f"SELECT {_SNAPSHOT_COLUMNS} FROM market_event_snapshots WHERE snapshot_id=%s",
            (snapshot_id,),
        )
        snapshot = await cur.fetchone()
        if snapshot is None:
            return None
        await cur.execute(
            f"""SELECT {",".join(f"f.{name.strip()}" for name in _FACT_COLUMNS.split(","))}
FROM market_event_snapshot_facts sf JOIN market_event_facts f USING(fact_id)
WHERE sf.snapshot_id=%s ORDER BY sf.ordinal""",
            (snapshot_id,),
        )
        facts = [dict(row) for row in await cur.fetchall()]
    return dict(snapshot), facts


async def get_raw_event(
    conn: AsyncConnection,
    event_id: UUID,
) -> dict[str, Any] | None:
    """Load raw evidence only for the trusted extraction boundary."""
    async with conn.cursor() as cur:
        await cur.execute(
            f"SELECT {_RAW_COLUMNS} FROM raw_market_events WHERE event_id=%s",
            (event_id,),
        )
        row = await cur.fetchone()
    return dict(row) if row else None


async def coverage(conn: AsyncConnection) -> dict[str, Any]:
    """Return source freshness and global ledger counts for operations."""
    async with conn.cursor() as cur:
        await cur.execute(
            """SELECT source,count(*) AS raw_event_count,count(*) FILTER(WHERE retracted) AS retractions,
max(accepted_at) AS latest_accepted_at,max(version) AS max_version
FROM raw_market_events GROUP BY source ORDER BY source"""
        )
        sources = [dict(row) for row in await cur.fetchall()]
        await cur.execute(
            """SELECT (SELECT count(*) FROM raw_market_events) AS raw_event_count,
(SELECT count(*) FROM market_event_facts) AS fact_count,
(SELECT count(*) FROM raw_market_events WHERE retracted) +
 (SELECT count(*) FROM market_event_facts WHERE retracted) AS retraction_count,
(SELECT max(accepted_at) FROM raw_market_events) AS latest_accepted_at"""
        )
        totals = await cur.fetchone()
    return {"as_of": datetime.now(UTC), "sources": sources, **dict(totals or {})}


__all__ = [
    "coverage",
    "create_snapshot",
    "get_raw_event",
    "get_snapshot",
    "ingest_raw_event",
    "write_fact",
]
