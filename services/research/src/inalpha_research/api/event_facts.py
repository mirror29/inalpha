"""Deterministic, tenant-neutral market-event fact extraction."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends

from ..config import ResearchSettings, get_research_settings
from ..data_client import DataClient
from ..event_extractor import extract_event_fact
from ..event_review import ReviewEventFactRequest, ReviewEventFactResponse, review_event_fact
from ..schemas import (
    ExtractedEventFact,
    ExtractEventFactsRequest,
    ExtractEventFactsResponse,
)
from ..service_auth import require_event_extract_request
from ..service_tokens import mint_data_event_token

router = APIRouter(prefix="/event-facts", tags=["event-facts"])

@router.post("/extract", response_model=ExtractEventFactsResponse)
async def extract_event_facts(
    body: ExtractEventFactsRequest,
    settings: Annotated[ResearchSettings, Depends(get_research_settings)],
    _caller: Annotated[str, Depends(require_event_extract_request)],
) -> ExtractEventFactsResponse:
    """Extract bounded facts without sending untrusted news text to any LLM."""
    token = mint_data_event_token(settings)
    facts: list[ExtractedEventFact] = []
    failed: list[Any] = []
    async with DataClient(settings.data_service_url, token) as data:
        for raw_event_id in body.raw_event_ids:
            try:
                raw = await data.get_raw_event(str(raw_event_id))
                payload = extract_event_fact(raw, body.policy_version)
                result = await data.write_event_fact(payload)
                fact = result["fact"]
                facts.append(
                    ExtractedEventFact(
                        fact_id=fact["fact_id"],
                        raw_event_id=fact["raw_event_id"],
                        event_type=fact["event_type"],
                        assets=fact["assets"],
                        action=fact["action"],
                        effective_at=fact["effective_at"],
                        available_at=fact["available_at"],
                        evidence_ids=[
                            f"{fact['fact_id']}:{index}"
                            for index, _span in enumerate(fact["evidence_spans"])
                        ],
                        created=bool(result["created"]),
                    )
                )
            except Exception:
                failed.append(raw_event_id)
    return ExtractEventFactsResponse(facts=facts, failed_event_ids=failed)


@router.post("/review", response_model=ReviewEventFactResponse)
async def review_legacy_event_fact(
    body: ReviewEventFactRequest,
    settings: Annotated[ResearchSettings, Depends(get_research_settings)],
    caller: Annotated[str, Depends(require_event_extract_request)],
) -> ReviewEventFactResponse:
    """Preview/apply one audited review under existing platform extraction authority."""
    async with DataClient(settings.data_service_url, mint_data_event_token(settings)) as data:
        return await review_event_fact(data, body, caller=caller)


__all__ = ["router"]
