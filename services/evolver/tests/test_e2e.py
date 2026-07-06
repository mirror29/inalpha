"""E2E 测试 —— 完整闭环。

测试策略（E1 不接真实 DB/LLM）：
1. POST /runs { budget: 2 } 返回 202
2. 响应体含 run_id + 正确状态
3. GET /runs/{id} 返回状态
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from inalpha_evolver.main import app


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def test_health_check(client: TestClient) -> None:
    """健康检查端点。"""
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["service"] == "inalpha-evolver"


def test_start_run(client: TestClient) -> None:
    """POST /runs 启动演化。"""
    payload = {
        "seed_strategy_id": "sma_cross_v1",
        "budget": 2,
        "config": {
            "universe": ["BTCUSDT"],
            "period_from": "2025-01-01",
            "period_to": "2025-12-31",
            "timeframe": "1h",
            "initial_cash": 10000.0,
        },
    }
    response = client.post("/api/v1/runs", json=payload)
    assert response.status_code == 202
    data = response.json()
    assert "run_id" in data
    assert data["status"] in ("completed", "failed")
    assert data["budget"] == 2


def test_start_run_minimal(client: TestClient) -> None:
    """POST /runs 最少参数。"""
    response = client.post("/api/v1/runs", json={})
    assert response.status_code == 202
    data = response.json()
    assert data["status"] in ("completed", "failed")
    assert data["budget"] == 4  # 默认


def test_get_run(client: TestClient) -> None:
    """GET /runs/{id} 返回正确状态。"""
    # 先启动一个 run
    resp = client.post("/api/v1/runs", json={"budget": 1})
    assert resp.status_code == 202
    run_id = resp.json()["run_id"]

    # 再查询
    resp2 = client.get(f"/api/v1/runs/{run_id}")
    assert resp2.status_code == 200
    assert resp2.json()["run_id"] == run_id


def test_get_nonexistent_run(client: TestClient) -> None:
    """GET /runs/{id} 不存在时返回 404。"""
    import uuid

    resp = client.get(f"/api/v1/runs/{uuid.uuid4()}")
    assert resp.status_code == 404