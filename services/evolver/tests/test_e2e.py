"""Evolver API DB/auth 契约测试。"""

from __future__ import annotations

import os
import time
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import jwt
import pytest
from fastapi.testclient import TestClient

from inalpha_evolver.api.request_hash import approval_request_digest
from inalpha_evolver.api.schemas import StartRunRequest
from inalpha_evolver.config import get_evolver_settings
from inalpha_evolver.main import app

from .llm_snapshot_fixtures import (
    EVOLUTION_GRANT_PUBLIC_KEY_B64,
    approval_token,
    llm_snapshot,
)

_SECRET = "evolver-test-secret-at-least-32-bytes-long"


def _headers(
    payload: dict | None = None,
    key: str | None = None,
    *,
    include_grant: bool = True,
) -> dict[str, str]:
    subject = f"test:{uuid4()}"
    token = jwt.encode(
        {"sub": subject, "exp": int(time.time()) + 3600},
        _SECRET,
        algorithm="HS256",
    )
    headers = {"Authorization": f"Bearer {token}"}
    if key:
        headers["Idempotency-Key"] = key
        if include_grant and payload is not None:
            request = StartRunRequest.model_validate(payload)
            headers["X-Evolution-Credential"] = approval_token(
                subject=subject,
                operation_id=key,
                request_digest=approval_request_digest(request),
            )
    return headers


@pytest.fixture(scope="module")
def client() -> TestClient:
    database_url = os.environ.get("EVOLVER_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("EVOLVER_TEST_DATABASE_URL is required for database integration tests")
    os.environ["DATABASE_URL"] = database_url
    os.environ["JWT_SECRET"] = _SECRET
    os.environ["EVOLUTION_CREDENTIAL_PUBLIC_KEY_B64"] = EVOLUTION_GRANT_PUBLIC_KEY_B64
    get_evolver_settings.cache_clear()
    with TestClient(app) as value:
        yield value
    get_evolver_settings.cache_clear()


def _payload() -> dict:
    now = datetime.now(UTC)
    return {
        "seed_strategy_id": "sma_cross_v1",
        "budget": 1,
        "config": {
            "venue": "binance",
            "symbol": "BTCUSDT",
            "timeframe": "1h",
            "from_ts": (now - timedelta(days=1)).isoformat(),
            "as_of": now.isoformat(),
            "initial_cash": 10_000,
        },
        "llm": llm_snapshot(),
    }


def test_business_endpoints_require_auth(client: TestClient) -> None:
    assert client.get("/api/v1/runs").status_code == 401


def test_start_requires_idempotency_key(client: TestClient) -> None:
    payload = _payload()
    assert client.post("/api/v1/runs", json=payload, headers=_headers(payload)).status_code == 400


def test_start_requires_explicit_approval_assertion(client: TestClient) -> None:
    payload = _payload()
    headers = _headers(payload, f"api-test-{uuid4()}", include_grant=False)
    assert client.post("/api/v1/runs", json=payload, headers=headers).status_code == 400


def test_start_rejects_approval_for_another_operation(client: TestClient) -> None:
    key = f"api-test-{uuid4()}"
    body = _payload()
    headers = _headers(body, key)
    token_payload = jwt.decode(
        headers["X-Evolution-Credential"], options={"verify_signature": False}
    )
    headers["X-Evolution-Credential"] = approval_token(
        subject=token_payload["sub"],
        operation_id=f"other-{uuid4()}",
        request_digest=approval_request_digest(StartRunRequest.model_validate(body)),
    )
    assert client.post("/api/v1/runs", json=body, headers=headers).status_code == 403


def test_start_rejects_business_request_tampered_after_approval(client: TestClient) -> None:
    key = f"api-test-{uuid4()}"
    payload = _payload()
    headers = _headers(payload, key)
    payload["budget"] = 2

    assert client.post("/api/v1/runs", json=payload, headers=headers).status_code == 403


def test_start_rejects_tampered_snapshot_before_approval(client: TestClient) -> None:
    key = f"api-test-{uuid4()}"
    payload = _payload()
    headers = _headers(payload, key)
    payload["llm"]["model"] = "tampered-model"
    assert client.post("/api/v1/runs", json=payload, headers=headers).status_code == 400


def test_start_returns_queued_and_is_idempotent(client: TestClient) -> None:
    key = f"api-test-{uuid4()}"
    payload = _payload()
    headers = _headers(payload, key)
    first = client.post("/api/v1/runs", json=payload, headers=headers)
    second = client.post("/api/v1/runs", json=payload, headers=headers)

    assert first.status_code == 202
    assert first.json()["status"] == "queued"
    assert second.status_code == 202
    assert second.json()["run_id"] == first.json()["run_id"]
