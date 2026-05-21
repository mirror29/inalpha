"""``GET /health`` 探活。"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.mark.integration
def test_health_ok(client: TestClient) -> None:
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["service"] == "data"
    assert body["db"] == "ok"


@pytest.mark.integration
def test_health_no_auth_required(client: TestClient) -> None:
    """/health 不需要 Authorization 头。"""
    r = client.get("/health")
    assert r.status_code != 401
