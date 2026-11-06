"""Atomic, prospective reviews of immutable legacy regulatory facts."""

import hashlib
from typing import Any
from uuid import UUID

from inalpha_shared.errors import ConflictError, InalphaError
from psycopg import AsyncConnection
from psycopg.rows import dict_row

from ..event_models import EventFactCorrectionRequest, EventFactWriteRequest
from .events import _FACT_COLUMNS, _advisory_lock_key, _canonical_hash, write_fact

_AUDIT_COLUMNS = "correction_id,source_fact_id,corrected_fact_id,retired_fact_id,request_hash,service_subject,reason,available_at"
_POLICY = "event-fact-correction-v1"


class CorrectionSourceNotFoundError(LookupError):
    """Only the exact missing source query maps to HTTP 404."""


def _invariant() -> InalphaError:
    """Retain audit guards even when Python optimization disables assertions."""
    return InalphaError(
        "correction could not preserve stored evidence", code="EVENT_FACT_CORRECTION_INVARIANT"
    )


def _conflict(message: str) -> ConflictError:
    """Fail closed rather than replacing a changed or unreviewed source version."""
    return ConflictError(message, code="EVENT_FACT_CORRECTION_CONFLICT")


async def correct_fact(
    conn: AsyncConnection,
    source_fact_id: UUID,
    request: EventFactCorrectionRequest,
    *,
    service_subject: str,
) -> tuple[dict[str, Any], dict[str, Any], bool]:
    """Retire the old classification and append its replacement/audit atomically; preserve snapshots.

    Availability comes from the database clock after serialization, never from a
    client or the original raw event's acceptance time. A replay returns the same
    appended version; changed input, identity or source versions are rejected.
    """
    if not 1 <= len(service_subject) <= 500:
        raise ValueError("verified service subject length is invalid")
    digest = _canonical_hash(
        {
            "source_fact_id": source_fact_id,
            "service_subject": service_subject,
            "request": request.model_dump(mode="json"),
        }
    )
    async with conn.transaction(), conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
            (_advisory_lock_key("fact-correction", request.correction_id),),
        )
        await cur.execute(
            f"SELECT {_AUDIT_COLUMNS} FROM market_event_fact_corrections WHERE correction_id=%s",
            (request.correction_id,),
        )
        existing = await cur.fetchone()
        if existing is not None:
            if existing["request_hash"] != digest:
                raise _conflict("correction identity or reviewed input changed")
            await cur.execute(
                f"SELECT {_FACT_COLUMNS} FROM market_event_facts WHERE fact_id=%s",
                (existing["corrected_fact_id"],),
            )
            saved = await cur.fetchone()
            if saved is None:
                raise _invariant()
            return dict(existing), dict(saved), False
        await cur.execute(
            f"SELECT {','.join('f.' + c.strip() for c in _FACT_COLUMNS.split(','))},r.source,r.source_event_id "
            "FROM market_event_facts f JOIN raw_market_events r ON r.event_id=f.raw_event_id WHERE f.fact_id=%s",
            (source_fact_id,),
        )
        source = await cur.fetchone()
        if source is None:
            raise CorrectionSourceNotFoundError("source fact not found")
        await cur.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
            (_advisory_lock_key(source["source"], source["source_event_id"]),),
        )
        await cur.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
            (_advisory_lock_key(source["raw_event_id"], source["fact_key"]),),
        )
        await cur.execute(
            "SELECT f.fact_id FROM market_event_facts f JOIN raw_market_events r ON r.event_id=f.raw_event_id "
            "WHERE r.source=%s AND r.source_event_id=%s AND f.fact_key=%s "
            "ORDER BY f.available_at DESC,f.version DESC,f.fact_id LIMIT 1",
            (source["source"], source["source_event_id"], source["fact_key"]),
        )
        latest = await cur.fetchone()
        if (
            latest is None
            or latest["fact_id"] != source_fact_id
            or source["fact_hash"] != request.expected_fact_hash
        ):
            raise _conflict("source is no longer the exact latest reviewed fact")
        if (
            source["retracted"]
            or source["event_type"] != "regulatory"
            or source["extractor_version"] != "deterministic-event-extractor-v2"
        ):
            raise _conflict("source is not an active legacy regulatory fact")
        if not request.value.retracted:
            await cur.execute(
                "SELECT event_id FROM raw_market_events WHERE source=%s AND source_event_id=%s ORDER BY version DESC LIMIT 1",
                (source["source"], source["source_event_id"]),
            )
            latest_raw = await cur.fetchone()
            if latest_raw is None or latest_raw["event_id"] != source["raw_event_id"]:
                raise _conflict(
                    "new raw evidence exists; review the latest extraction or explicitly retire the obsolete classification"
                )
        await cur.execute("SELECT clock_timestamp() AS available_at")
        clock = await cur.fetchone()
        if clock is None:
            raise _invariant()
        available_at = clock["available_at"]
        if source["available_at"] > available_at or source["created_at"] > available_at:
            raise _conflict("source fact is not yet available for review")
        submitted = request.value.model_dump(mode="json")
        if sorted({asset.strip().upper() for asset in submitted["assets"]}) != source[
            "assets"
        ] or any(submitted[key] != source[key] for key in ("actor", "action", "evidence_spans")):
            raise _conflict(
                "classification review must preserve source evidence and asset identity"
            )
        if not request.value.retracted:
            await cur.execute(
                "WITH latest AS (SELECT DISTINCT ON(f.fact_key) f.fact_key,f.event_type,f.retracted "
                "FROM market_event_facts f JOIN raw_market_events r ON r.event_id=f.raw_event_id "
                "WHERE r.source=%s AND r.source_event_id=%s AND f.available_at<=%s "
                "ORDER BY f.fact_key,f.available_at DESC,f.version DESC,f.fact_id) "
                "SELECT fact_key FROM latest WHERE fact_key<>%s AND event_type=%s AND NOT retracted LIMIT 1",
                (
                    source["source"],
                    source["source_event_id"],
                    available_at,
                    source["fact_key"],
                    request.value.event_type,
                ),
            )
            if await cur.fetchone() is not None:
                raise _conflict(
                    "corrected classification already has another visible key; review an explicit retraction"
                )
        original_key = hashlib.sha256(
            f"{source['source']}\0{source['source_event_id']}\0{source['event_type']}".encode()
        ).hexdigest()[:48]
        if source["fact_key"] != original_key:
            raise _conflict("source fact key is not the recognized deterministic identity")
        new_key = hashlib.sha256(
            f"{source['source']}\0{source['source_event_id']}\0{request.value.event_type}".encode()
        ).hexdigest()[:48]
        retired = None
        if request.value.retracted or new_key != original_key:
            retirement = EventFactWriteRequest.model_validate(
                {
                    **{
                        key: source[key]
                        for key in (
                            "raw_event_id",
                            "fact_key",
                            "event_type",
                            "assets",
                            "actor",
                            "action",
                            "severity",
                            "confidence",
                            "effective_at",
                            "evidence_spans",
                        )
                    },
                    "available_at": available_at,
                    "extractor_version": request.value.extractor_version,
                    "policy_version": _POLICY,
                    "retracted": True,
                }
            )
            retired, _ = await write_fact(conn, retirement)
        if request.value.retracted:
            if retired is None:
                raise _invariant()
            fact = retired
        else:
            fact, created = await write_fact(
                conn,
                EventFactWriteRequest(
                    **request.value.model_dump(),
                    raw_event_id=source["raw_event_id"],
                    fact_key=new_key,
                    effective_at=source["effective_at"],
                    available_at=available_at,
                    policy_version=_POLICY,
                ),
            )
            if not created:
                raise _invariant()
        await cur.execute(
            f"INSERT INTO market_event_fact_corrections({_AUDIT_COLUMNS}) VALUES(%s,%s,%s,%s,%s,%s,%s,%s) RETURNING {_AUDIT_COLUMNS}",
            (
                request.correction_id,
                source_fact_id,
                fact["fact_id"],
                retired["fact_id"] if retired else None,
                digest,
                service_subject,
                request.reason,
                available_at,
            ),
        )
        audit = await cur.fetchone()
        if audit is None:
            raise _invariant()
    return dict(audit), fact, True
