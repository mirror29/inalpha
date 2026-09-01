"""Point-in-time market event ledger and frozen snapshot API."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from inalpha_shared.auth import User, get_current_user
from inalpha_shared.db import DBConn
from inalpha_shared.errors import InalphaError, ValidationError

from ..connectors.coinmarketcal import get_connector
from ..event_models import (
    CoinMarketCalImportRequest,
    EventCoverageResponse,
    EventFactRecord,
    EventFactWriteRequest,
    EventFactWriteResponse,
    EventImportResponse,
    EventSnapshotRecord,
    EventSnapshotRequest,
    RawEventIngestRequest,
    RawEventIngestResponse,
    RawEventRecord,
)
from ..storage import events as store

router = APIRouter(prefix="/events", tags=["events"])


class EventRecordNotFoundError(InalphaError):
    """Requested raw event or immutable snapshot does not exist."""

    code = "EVENT_RECORD_NOT_FOUND"
    status_code = 404


class EventProviderUnavailableError(InalphaError):
    """Configured historical event provider cannot serve this request."""

    code = "EVENT_PROVIDER_UNAVAILABLE"
    status_code = 503


@router.post("/raw", response_model=RawEventIngestResponse)
async def ingest_raw_event(
    request: RawEventIngestRequest,
    db: DBConn,
    _user: Annotated[User, Depends(get_current_user)],
) -> RawEventIngestResponse:
    """Append one raw event version; identical retries return the existing version."""
    row, created = await store.ingest_raw_event(db, request)
    return RawEventIngestResponse(event=RawEventRecord(**row), created=created)


@router.post("/facts", response_model=EventFactWriteResponse)
async def write_event_fact(
    request: EventFactWriteRequest,
    db: DBConn,
    _user: Annotated[User, Depends(get_current_user)],
) -> EventFactWriteResponse:
    """Append one fact version without exposing its source content downstream."""
    try:
        row, created = await store.write_fact(db, request)
    except LookupError as exc:
        raise EventRecordNotFoundError(
            f"raw event {request.raw_event_id} not found",
            details={"raw_event_id": str(request.raw_event_id)},
        ) from exc
    except ValueError as exc:
        raise ValidationError(
            str(exc),
            code="EVENT_AVAILABLE_AT_INVALID",
        ) from exc
    return EventFactWriteResponse(fact=EventFactRecord(**row), created=created)


@router.get("/raw/{event_id}", response_model=RawEventRecord)
async def get_raw_event(
    event_id: UUID,
    db: DBConn,
    _user: Annotated[User, Depends(get_current_user)],
) -> RawEventRecord:
    """Expose raw evidence only to authenticated platform extraction services."""
    row = await store.get_raw_event(db, event_id)
    if row is None:
        raise EventRecordNotFoundError(
            f"raw event {event_id} not found",
            details={"raw_event_id": str(event_id)},
        )
    return RawEventRecord(**row)


@router.post("/snapshots", response_model=EventSnapshotRecord)
async def create_event_snapshot(
    request: EventSnapshotRequest,
    db: DBConn,
    _user: Annotated[User, Depends(get_current_user)],
) -> EventSnapshotRecord:
    """Freeze latest visible event facts at ``cutoff`` with deterministic ordering."""
    snapshot, facts = await store.create_snapshot(db, request)
    return EventSnapshotRecord(
        **snapshot,
        facts=[EventFactRecord(**row) for row in facts],
    )


@router.get("/snapshots/{snapshot_id}", response_model=EventSnapshotRecord)
async def get_event_snapshot(
    snapshot_id: UUID,
    db: DBConn,
    _user: Annotated[User, Depends(get_current_user)],
) -> EventSnapshotRecord:
    """Load a frozen snapshot; facts preserve their original stable ordinal."""
    result = await store.get_snapshot(db, snapshot_id)
    if result is None:
        raise EventRecordNotFoundError(
            f"event snapshot {snapshot_id} not found",
            details={"snapshot_id": str(snapshot_id)},
        )
    snapshot, facts = result
    return EventSnapshotRecord(
        **snapshot,
        facts=[EventFactRecord(**row) for row in facts],
    )


@router.get("/coverage", response_model=EventCoverageResponse)
async def get_event_coverage(
    db: DBConn,
    _user: Annotated[User, Depends(get_current_user)],
) -> EventCoverageResponse:
    """Return source freshness, versions, and retractions for operational monitoring."""
    return EventCoverageResponse(**await store.coverage(db))


@router.post("/import/coinmarketcal", response_model=EventImportResponse)
async def import_coinmarketcal(
    request: CoinMarketCalImportRequest,
    db: DBConn,
    _user: Annotated[User, Depends(get_current_user)],
) -> EventImportResponse:
    """Import a bounded Professional catalog window into the immutable raw ledger."""
    connector = get_connector()
    if not connector.configured:
        raise EventProviderUnavailableError(
            "CoinMarketCal Professional API is not configured",
            details={"env": "COINMARKETCAL_API_KEY"},
        )
    try:
        records = await connector.fetch(request)
    except Exception as exc:
        raise EventProviderUnavailableError(
            f"CoinMarketCal import failed: {type(exc).__name__}",
        ) from exc
    created = unchanged = failed = 0
    for record in records:
        try:
            async with db.transaction():
                _, was_created = await store.ingest_raw_event(db, record)
        except Exception:
            failed += 1
            continue
        created += int(was_created)
        unchanged += int(not was_created)
    return EventImportResponse(
        source="coinmarketcal",
        fetched=len(records),
        created=created,
        unchanged=unchanged,
        failed=failed,
    )


__all__ = ["router"]
