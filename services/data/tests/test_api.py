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


# ─── /ticker ───


def test_ticker_requires_auth(client: TestClient) -> None:
    r = client.get("/ticker", params={"symbol": "BTC/USDT"})
    assert r.status_code == 401


def test_ticker_returns_404_when_no_price(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """新 symbol 在 DB 里没数据 → 404 NO_PRICE_AVAILABLE。"""
    r = client.get(
        "/ticker",
        headers=auth_headers,
        params={"venue": "binance", "symbol": f"GHOST/USDT-{uuid4().hex[:8]}"},
    )
    assert r.status_code == 404
    body = r.json()
    assert body["code"] == "NO_PRICE_AVAILABLE"
    assert "backfill" in body["details"]["hint"]


def test_ticker_returns_latest_bar_close(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """backfill 5 根 1h bar，ticker 取最新一根的 close。"""
    symbol = f"TICK/{uuid4().hex[:8]}"
    from_ts = "2026-04-01T00:00:00Z"
    to_ts = "2026-04-01T05:00:00Z"

    client.post(
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

    r = client.get(
        "/ticker",
        headers=auth_headers,
        params={"venue": "binance", "symbol": symbol},
    )
    # 注意：mock connector 生成的 bar 时间是 2026-04-01，相对"现在"已是 stale，
    # 24h lookback 拿不到。这种场景应该 404
    assert r.status_code == 404
    assert r.json()["code"] == "NO_PRICE_AVAILABLE"


def test_ticker_returns_fresh_bar_within_lookback(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """直接插一根"现在的" 1h bar，ticker 应返 200 + 该价格 + is_stale=False。"""
    import asyncio
    from datetime import UTC, datetime, timedelta

    from inalpha_shared.db import get_conn

    from inalpha_data.storage.bars import insert_bars

    symbol = f"FRESH/{uuid4().hex[:8]}"
    now = datetime.now(UTC).replace(microsecond=0, second=0)
    # 模拟 1 分钟前的一根（fresh：低于 5 分钟 stale 阈值）
    bar_ts = now - timedelta(minutes=1)

    async def _insert() -> None:
        async with get_conn() as conn:
            await insert_bars(
                conn,
                "binance",
                symbol,
                "1h",
                [(bar_ts, 100.0, 101.0, 99.0, 100.5, 1.0)],
            )

    asyncio.run(_insert())

    r = client.get(
        "/ticker",
        headers=auth_headers,
        params={"venue": "binance", "symbol": symbol},
    )
    assert r.status_code == 200, r.json()
    body = r.json()
    assert body["venue"] == "binance"
    assert body["symbol"] == symbol
    assert body["price"] == 100.5
    assert body["source"] == "db_1h"
    assert body["is_stale"] is False
    assert body["stale_seconds"] < 120  # 约 1 分钟 ± 容差
