"""Terminal Forward evidence must never bypass signature verification."""

from uuid import uuid4

import httpx
import pytest

from inalpha_evolver import forward_client
from inalpha_evolver.config import EvolverSettings


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["passed", "failed", "insufficient_evidence"])
async def test_unsigned_terminal_evidence_without_finish_time_is_rejected(monkeypatch, status):
    sandbox_id, campaign_id, candidate_id = uuid4(), uuid4(), uuid4()
    payload = {
        "sandbox_id": str(sandbox_id), "campaign_id": str(campaign_id),
        "candidate_id": str(candidate_id), "status": status, "event_count": 3,
        "evidence_version": 1, "finished_at": None,
    }
    original_client = httpx.AsyncClient
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=payload))
    monkeypatch.setattr(forward_client.httpx, "AsyncClient", lambda **kwargs: original_client(
        **kwargs, transport=transport,
    ))
    with pytest.raises(RuntimeError, match="evidence"):
        await forward_client.get_forward_sandbox(
            {"forward_sandbox_id": sandbox_id, "campaign_id": campaign_id,
             "locked_candidate_id": candidate_id, "owner_account_id": uuid4()},
            EvolverSettings(JWT_SECRET="test-forward-evidence-secret-at-least-32-bytes"),
        )
