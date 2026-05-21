"""端到端 API 测试 —— 启动真实 app（含 lifespan）+ mocked Binance connector。"""
from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.integration


def test_bars_query_requires_auth(client: TestClient) -> None:
    r = client.get("/bars", params={
        "symbol": "BTC/USDT",
        "from_ts": "2026-01-01T00:00:00Z",
        "to_ts": "2026-01-02T00:00:00Z",
    })
    assert r.status_code == 401
    assert r.json()["code"] == "UNAUTHORIZED"


def test_backfill_requires_auth(client: TestClient) -> None:
    r = client.post("/backfill/bars", json={
        "symbol": "BTC/USDT",
        "timeframe": "1h",
        "from_ts": "2026-01-01T00:00:00Z",
        "to_ts": "2026-01-02T00:00:00Z",
    })
    assert r.status_code == 401


def test_backfill_then_query_round_trip(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """跑 backfill（用 mock connector 生成 5 根），再 query 出来对一致。"""
    symbol = f"E2E/{uuid4().hex[:8]}"
    from_ts = "2026-04-01T00:00:00Z"
    to_ts = "2026-04-01T05:00:00Z"

    # 1. backfill
    r = client.post(
        "/backfill/bars",
        headers=auth_headers,
        json={
            "venue": "binance",
            "symbol": symbol,
            "timeframe": "1h",
            "from_ts": from_ts,
            "to_ts": to_ts,
        },
    )
    assert r.status_code == 200, r.json()
    body = r.json()
    assert body["venue"] == "binance"
    assert body["symbol"] == symbol
    assert body["bars_fetched"] >= 5  # mock 每次返回 5 根

    # 2. query
    r = client.get(
        "/bars",
        headers=auth_headers,
        params={
            "venue": "binance",
            "symbol": symbol,
            "timeframe": "1h",
            "from_ts": from_ts,
            "to_ts": to_ts,
        },
    )
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) >= 5
    # 单调递增的 close 价 (mock 生成的)
    closes = [r["close"] for r in rows]
    assert closes == sorted(closes)


def test_query_validates_time_range(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    r = client.get(
        "/bars",
        headers=auth_headers,
        params={
            "symbol": "BTC/USDT",
            "from_ts": "2026-02-01T00:00:00Z",
            "to_ts": "2026-01-01T00:00:00Z",  # 反了
        },
    )
    assert r.status_code == 400
    assert r.json()["code"] == "VALIDATION_ERROR"


def test_backfill_rejects_unknown_venue(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    r = client.post(
        "/backfill/bars",
        headers=auth_headers,
        json={
            "venue": "bitfinex",
            "symbol": "BTC/USDT",
            "timeframe": "1h",
            "from_ts": "2026-01-01T00:00:00Z",
            "to_ts": "2026-01-02T00:00:00Z",
        },
    )
    assert r.status_code == 400
    assert r.json()["code"] == "VALIDATION_ERROR"
    assert "binance" in r.json()["message"]
