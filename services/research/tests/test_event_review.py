"""Legacy review is explicit, digest-bound, service-only, and never backdated."""

import hashlib
import time
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import jwt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from inalpha_shared import install_error_handler
from inalpha_shared.errors import ConflictError

from inalpha_research.api import event_facts
from inalpha_research.config import ResearchSettings, get_research_settings
from inalpha_research.data_client import DataClient, DataServiceError
from inalpha_research.event_extractor import extract_event_fact
from inalpha_research.event_review import ReviewEventFactRequest, review_event_fact


def evidence():
    raw = {
        "event_id": str(uuid4()),
        "source": "test",
        "source_event_id": "article",
        "title": "Bitcoin bank custody",
        "content": "",
        "first_seen_at": "2026-10-13T00:00:00Z",
        "accepted_at": "2026-10-13T00:00:01Z",
    }
    source = {
        **extract_event_fact(raw, "first-seen-only-v1"),
        "fact_id": str(uuid4()),
        "fact_hash": "a" * 64,
        "event_type": "regulatory",
        "extractor_version": "deterministic-event-extractor-v2",
    }
    source["fact_key"] = hashlib.sha256(b"test\0article\0regulatory").hexdigest()[:48]
    return raw, source


def request(source):
    return ReviewEventFactRequest(
        fact_id=source["fact_id"],
        expected_fact_hash=source["fact_hash"],
        correction_id=uuid4(),
        reason="Review legacy bank substring against v3 rules",
    )


def client(raw, source):
    data = AsyncMock(spec=DataClient)
    data.get_event_fact.return_value = source
    data.get_raw_event.return_value = raw
    data.correct_event_fact.return_value = {
        "created": True,
        "correction": {"correction_id": "saved"},
    }
    return data


@pytest.mark.asyncio
async def test_preview_has_no_write_and_apply_sends_no_client_availability():
    raw, source = evidence()
    data = client(raw, source)
    body = request(source)
    preview = await review_event_fact(data, body, caller="platform-reviewer")
    assert not preview.applied and preview.reviewed_event_type == "other"
    data.correct_event_fact.assert_not_awaited()
    applied = await review_event_fact(
        data,
        body.model_copy(
            update={
                "dry_run": False,
                "expected_review_hash": preview.review_hash,
            }
        ),
        caller="platform-reviewer",
    )
    assert applied.applied and applied.result == data.correct_event_fact.return_value
    args = data.correct_event_fact.call_args.args
    assert args[0] == str(body.fact_id)
    assert args[1]["correction_id"] == str(body.correction_id)
    assert (
        not {"available_at", "effective_at", "fact_key", "raw_event_id"} & args[1]["value"].keys()
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["missing_digest", "reason", "caller", "retract", "operation"])
async def test_apply_rejects_changed_preview(change):
    raw, source = evidence()
    data = client(raw, source)
    body = request(source)
    preview = await review_event_fact(data, body, caller="platform-reviewer")
    changes = {"dry_run": False, "expected_review_hash": preview.review_hash}
    caller = "platform-reviewer"
    if change == "missing_digest":
        changes["expected_review_hash"] = None
    if change == "reason":
        changes["reason"] = "different review"
    if change == "retract":
        changes["retract"] = True
    if change == "operation":
        changes["correction_id"] = uuid4()
    if change == "caller":
        caller = "different-platform-reviewer"
    with pytest.raises(ConflictError):
        await review_event_fact(data, body.model_copy(update=changes), caller=caller)
    data.correct_event_fact.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field,value",
    [
        ("fact_hash", "b" * 64),
        ("retracted", True),
        ("extractor_version", "unknown"),
        ("action", "changed action"),
        ("assets", ["ETH"]),
    ],
)
async def test_review_rejects_changed_source_or_evidence(field, value):
    raw, source = evidence()
    body = request(source)
    data = client(raw, {**source, field: value})
    with pytest.raises(ConflictError):
        await review_event_fact(data, body, caller="platform-reviewer")
    data.correct_event_fact.assert_not_awaited()


@pytest.mark.asyncio
async def test_retracted_raw_cannot_produce_active_replacement():
    raw, source = evidence()
    raw["retracted"] = True
    preview = await review_event_fact(
        client(raw, source), request(source), caller="platform-reviewer"
    )
    assert preview.retract


@pytest.mark.parametrize("mode", ["missing", "owner", "wrong_purpose", "service"])
def test_review_route_requires_existing_platform_authority(monkeypatch, mode):
    secret = "event-review-only-test-secret-not-production"
    app = FastAPI()
    install_error_handler(app)
    app.include_router(event_facts.router)
    app.dependency_overrides[get_research_settings] = lambda: ResearchSettings(
        DATABASE_URL="postgresql://unused",
        JWT_SECRET=secret,
    )
    mocked = AsyncMock(
        return_value={
            "review_hash": "a" * 64,
            "source_fact_id": str(uuid4()),
            "original_event_type": "regulatory",
            "reviewed_event_type": "other",
            "extractor_version": "deterministic-event-extractor-v3",
            "retract": False,
            "applied": False,
        }
    )
    monkeypatch.setattr(event_facts, "review_event_fact", mocked)
    headers = {}
    if mode != "missing":
        now = time.time()
        claims = {"sub": "platform-reviewer", "iat": now, "exp": now + 60}
        if mode != "owner":
            claims.update(
                token_use="service",
                service_audience="research",
                token_purpose="event_extract_request" if mode == "service" else "wrong",
            )
        headers["Authorization"] = "Bearer " + jwt.encode(claims, secret, algorithm="HS256")
    _, source = evidence()
    with TestClient(app) as browser:
        response = browser.post(
            "/event-facts/review", json=request(source).model_dump(mode="json"), headers=headers
        )
    assert response.status_code == (200 if mode == "service" else 401)
    assert mocked.await_count == (1 if mode == "service" else 0)


@pytest.mark.asyncio
async def test_lost_correction_response_is_not_automatically_resent():
    calls = []

    async def handler(req):
        calls.append(req)
        raise httpx.ReadError("synthetic lost acknowledgement")

    async with DataClient("http://data.test", "fixture") as data:
        await data._client.aclose()
        data._client = httpx.AsyncClient(
            base_url="http://data.test", transport=httpx.MockTransport(handler)
        )
        with pytest.raises(httpx.ReadError):
            await data.correct_event_fact(str(uuid4()), {})
    assert len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mode", ["valid", "wrong_operation", "missing_ids", "missing_audit", "missing_fact_fields"]
)
async def test_correction_acknowledgement_is_bound_to_exact_operation(mode):
    source_id, correction_id, replacement_id = map(str, (uuid4(), uuid4(), uuid4()))
    raw, _source = evidence()
    fact = {
        **extract_event_fact(raw, "event-fact-correction-v1"),
        "fact_id": replacement_id,
        "version": 1,
        "fact_hash": "b" * 64,
        "asset_ids": ["asset:BTC"],
        "supersedes_fact_id": None,
        "created_at": "2026-10-14T00:00:00Z",
    }
    audit = {
        "correction_id": correction_id if mode != "wrong_operation" else str(uuid4()),
        "source_fact_id": source_id,
        "corrected_fact_id": replacement_id,
        "request_hash": "c" * 64,
        "service_subject": "service:research",
        "reason": "verified fixture review",
        "available_at": "2026-10-14T00:00:00Z",
    }
    if mode == "missing_ids":
        audit.pop("corrected_fact_id")
        fact = {}
    if mode == "missing_audit":
        audit.pop("request_hash")
    if mode == "missing_fact_fields":
        fact.pop("available_at")

    async def handler(req):
        return httpx.Response(
            200,
            json={
                "created": True,
                "correction": audit,
                "fact": fact,
            },
        )

    async with DataClient("http://data.test", "fixture") as data:
        await data._client.aclose()
        data._client = httpx.AsyncClient(
            base_url="http://data.test", transport=httpx.MockTransport(handler)
        )
        if mode == "valid":
            result = await data.correct_event_fact(source_id, {"correction_id": correction_id})
            assert result["correction"]["corrected_fact_id"] == replacement_id
        else:
            with pytest.raises(DataServiceError) as caught:
                await data.correct_event_fact(source_id, {"correction_id": correction_id})
            assert caught.value.code == "EVENT_FACT_CORRECTION_RESPONSE_UNKNOWN"
