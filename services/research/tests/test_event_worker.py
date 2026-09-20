"""Durable event extraction worker behavior tests."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from inalpha_research import event_worker
from inalpha_research.config import ResearchSettings


def _settings() -> ResearchSettings:
    return ResearchSettings(
        DATABASE_URL="postgresql://unused",
        JWT_SECRET="research-event-worker-test-secret",
        EVENT_EXTRACTION_BATCH_SIZE=5,
        EVENT_EXTRACTION_LEASE_SECONDS=60,
    )


@pytest.mark.asyncio
async def test_worker_claims_extracts_writes_and_fences_completion(monkeypatch) -> None:
    now = datetime.now(UTC).isoformat()
    calls: list[tuple[str, Any]] = []

    class FakeDataClient:
        def __init__(self, _url: str, _token: str) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def claim_extraction_jobs(self, **kwargs: Any) -> list[dict[str, Any]]:
            calls.append(("claim", kwargs))
            return [{
                "job_id": "11111111-1111-4111-8111-111111111111",
                "lease_token": "22222222-2222-4222-8222-222222222222",
                "raw_event": {
                    "event_id": "33333333-3333-4333-8333-333333333333",
                    "source": "official-exchange",
                    "source_event_id": "listing-1",
                    "title": "Exchange listing BTC",
                    "content": "New BTC pair",
                    "raw_payload": {"symbol": "BTC"},
                    "source_valid_at": now,
                    "claimed_published_at": now,
                    "first_seen_at": now,
                    "accepted_at": now,
                    "policy_version": "first-seen-only-v1",
                    "retracted": False,
                },
            }]

        async def write_event_fact(self, payload: dict[str, Any]) -> dict[str, Any]:
            calls.append(("write", payload))
            return {"fact": payload, "created": True}

        async def complete_extraction_job(self, **kwargs: Any) -> dict[str, Any]:
            calls.append(("complete", kwargs))
            return kwargs

    monkeypatch.setattr(event_worker, "DataClient", FakeDataClient)

    completed = await event_worker.run_extraction_once(
        _settings(),
        worker_id="research:test",
    )

    assert completed == 1
    assert [name for name, _ in calls] == ["claim", "write", "complete"]
    assert calls[1][1]["event_type"] == "listing"
    assert calls[1][1]["assets"] == ["BTC"]
    assert calls[2][1] == {
        "job_id": "11111111-1111-4111-8111-111111111111",
        "lease_token": "22222222-2222-4222-8222-222222222222",
        "succeeded": True,
    }
