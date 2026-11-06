"""Bounded, explicit review of legacy facts using immutable archived evidence."""

import hashlib
import json
from typing import Any
from uuid import UUID

from inalpha_shared.errors import ConflictError
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .data_client import DataClient
from .event_extractor import EXTRACTOR_VERSION, extract_event_fact


class ReviewEventFactRequest(BaseModel):
    """Preview by default; applying requires the exact previously reviewed digest."""

    model_config = ConfigDict(extra="forbid")
    fact_id: UUID
    expected_fact_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    correction_id: UUID
    reason: str = Field(min_length=1, max_length=2_000)
    retract: bool = False
    dry_run: bool = True
    expected_review_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")

    @field_validator("reason")
    @classmethod
    def require_reason(cls, value: str) -> str:
        """Normalize the same nonempty reason that Data binds to its audit record."""
        if not value.strip():
            raise ValueError("review reason must contain text")
        return value.strip()


class ReviewEventFactResponse(BaseModel):
    """Classification preview or an acknowledged correction; no execution authority."""

    review_hash: str
    source_fact_id: UUID
    original_event_type: str
    reviewed_event_type: str
    extractor_version: str
    retract: bool
    applied: bool
    result: dict[str, Any] | None = None


async def review_event_fact(
    data: DataClient, request: ReviewEventFactRequest, *, caller: str
) -> ReviewEventFactResponse:
    """Recompute one review from exact raw evidence; never sweep or backdate facts."""
    source = await data.get_event_fact(str(request.fact_id))
    if (
        str(source.get("fact_id")) != str(request.fact_id)
        or source.get("fact_hash") != request.expected_fact_hash
        or source.get("event_type") != "regulatory"
        or source.get("extractor_version") != "deterministic-event-extractor-v2"
        or source.get("retracted")
    ):
        raise ConflictError(
            "source is not the exact active legacy review version",
            code="EVENT_REVIEW_SOURCE_CHANGED",
        )
    raw = await data.get_raw_event(str(source["raw_event_id"]))
    if str(raw.get("event_id")) != str(source["raw_event_id"]):
        raise ConflictError("source raw identity changed", code="EVENT_REVIEW_EVIDENCE_CHANGED")
    extracted = extract_event_fact(raw, str(source["policy_version"]))
    if EXTRACTOR_VERSION != "deterministic-event-extractor-v3" or any(
        extracted[key] != source[key] for key in ("assets", "actor", "action", "evidence_spans")
    ):
        raise ConflictError(
            "review changed source evidence or unsupported extractor version",
            code="EVENT_REVIEW_EVIDENCE_CHANGED",
        )
    payload: dict[str, Any] = {
        "correction_id": str(request.correction_id),
        "expected_fact_hash": request.expected_fact_hash,
        "reason": request.reason,
        "value": {
            **{
                key: extracted[key]
                for key in (
                    "event_type",
                    "assets",
                    "actor",
                    "action",
                    "severity",
                    "confidence",
                    "evidence_spans",
                    "extractor_version",
                )
            },
            "retracted": request.retract or bool(raw.get("retracted")),
        },
    }
    digest = hashlib.sha256(
        json.dumps(
            {"source_fact_id": str(request.fact_id), "caller": caller, "payload": payload},
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode()
    ).hexdigest()
    result = None
    if not request.dry_run:
        if request.expected_review_hash != digest:
            raise ConflictError(
                "preview digest missing or changed; prepare a new review",
                code="EVENT_REVIEW_DIGEST_CHANGED",
            )
        result = await data.correct_event_fact(str(request.fact_id), payload)
    return ReviewEventFactResponse(
        review_hash=digest,
        source_fact_id=request.fact_id,
        original_event_type=source["event_type"],
        reviewed_event_type=extracted["event_type"],
        extractor_version=EXTRACTOR_VERSION,
        retract=payload["value"]["retracted"],
        applied=not request.dry_run,
        result=result,
    )
