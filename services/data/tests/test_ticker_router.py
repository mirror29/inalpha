"""``GET /ticker?fresh=true`` 多 venue 路由测试（D-9）。

D-9 ``ticker.py`` 从硬编码 binance 改成按 ``TickerCapable`` Protocol 鸭子分发。本测试覆盖：

- ``fetch_ticker`` 已实现的 venue（binance / yfinance / alpaca）走通 fresh=true 路径
- 已注册但未实现 ``fetch_ticker`` 的 venue（akshare / fred）→ 422
  FRESH_NOT_SUPPORTED_FOR_VENUE + hint 提示切 fresh=false
- venue 未注册 → 422 + supported 列表（与 /backfill/bars 错误形态一致）
- fresh=false 路径仍然支持任意 venue（走 DB cache，由 conftest 的 binance mock 覆盖原路径）
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.integration


class _TickerCapableFake:
    """fake connector，实现 ``TickerCapable`` —— fetch_ticker 返固定 (now, 123.45)。"""

    async def fetch_bars(
        self,
        symbol: str,
        timeframe: str,
        since: datetime,
        limit: int = 1000,
    ) -> list[tuple[datetime, float, float, float, float, float]]:
        return []

    async def fetch_ticker(self, symbol: str) -> tuple[datetime, float]:
        return datetime.now(UTC), 123.45

    async def close(self) -> None:
        pass


class _NoTickerFake:
    """fake connector，**只**实现 fetch_bars，不实现 fetch_ticker。

    模拟 akshare / fred 这类不支持实时 ticker 的 venue。
    """

    async def fetch_bars(
        self,
        symbol: str,
        timeframe: str,
        since: datetime,
        limit: int = 1000,
    ) -> list[tuple[datetime, float, float, float, float, float]]:
        return []

    async def close(self) -> None:
        pass


@pytest.fixture
async def app_with_ticker_capabilities() -> AsyncIterator[Any]:
    """覆盖 registry：binance/yfinance/alpaca 用 _TickerCapableFake；akshare/fred 用 _NoTickerFake。"""
    from inalpha_data.connectors import _base as _connectors_base
    from inalpha_data.main import app

    async with app.router.lifespan_context(app):
        for v in ("binance", "yfinance", "alpaca"):
            _connectors_base._REGISTRY[v] = _TickerCapableFake()
        for v in ("akshare", "fred"):
            _connectors_base._REGISTRY[v] = _NoTickerFake()
        yield app

    app.dependency_overrides.clear()


@pytest.fixture
def ticker_client(app_with_ticker_capabilities: Any) -> TestClient:
    return TestClient(app_with_ticker_capabilities)


# ─── fresh=true 在 TickerCapable venue 走通 ──────────────────────────


@pytest.mark.parametrize(
    "venue, symbol",
    [
        ("binance", "BTC/USDT"),
        ("yfinance", "TSLA"),
        ("alpaca", "AAPL"),
    ],
)
def test_ticker_fresh_true_routes_via_capability(
    ticker_client: TestClient,
    auth_headers: dict[str, str],
    venue: str,
    symbol: str,
) -> None:
    """3 个 TickerCapable venue × 各自 symbol → fresh=true 路径返 200 + 假价 + source 含 venue 名。

    回归用户报"特斯拉现价拿不到"：yfinance 走 TickerCapable 分支，不再被硬编码挡。
    """
    r = ticker_client.get(
        "/ticker",
        headers=auth_headers,
        params={"venue": venue, "symbol": symbol, "fresh": "true"},
    )
    assert r.status_code == 200, r.json()
    body = r.json()
    assert body["venue"] == venue
    assert body["symbol"] == symbol
    assert body["price"] == 123.45
    assert body["source"] == f"{venue}_ticker"
    # fake 返 now() → stale_seconds 应接近 0
    assert body["stale_seconds"] < 5
    assert body["is_stale"] is False


class _StaleTickerFake(_TickerCapableFake):
    """fetch_ticker 返 4 小时前的真实成交时间——模拟休市时段的 yfinance（issue #62）。"""

    async def fetch_ticker(self, symbol: str) -> tuple[datetime, float]:
        return datetime.now(UTC) - timedelta(hours=4), 123.45


def test_ticker_fresh_true_marks_stale_when_quote_time_old(
    app_with_ticker_capabilities: Any, auth_headers: dict[str, str]
) -> None:
    """connector 透出滞后报价时间 → is_stale=true（issue #62 回归：休市不再假新鲜）。

    修复前 yfinance fetch_ticker 用 now() 兜底，stale_seconds≈0 永不 stale，
    paper live runner 会把上一交易日收盘价当新鲜价下单。
    """
    from inalpha_data.connectors import _base as _connectors_base

    _connectors_base._REGISTRY["yfinance"] = _StaleTickerFake()
    client = TestClient(app_with_ticker_capabilities)
    r = client.get(
        "/ticker",
        headers=auth_headers,
        params={"venue": "yfinance", "symbol": "TSLA", "fresh": "true"},
    )
    assert r.status_code == 200, r.json()
    body = r.json()
    assert body["is_stale"] is True
    assert body["stale_seconds"] > 3 * 3600


# ─── 已注册但无 fetch_ticker 的 venue ───────────────────────────────


@pytest.mark.parametrize("venue", ["akshare", "fred"])
def test_ticker_fresh_true_returns_friendly_error_for_non_ticker_venue(
    ticker_client: TestClient, auth_headers: dict[str, str], venue: str
) -> None:
    """akshare / fred connector 没 fetch_ticker → 422 FRESH_NOT_SUPPORTED_FOR_VENUE + hint。"""
    r = ticker_client.get(
        "/ticker",
        headers=auth_headers,
        params={"venue": venue, "symbol": "X", "fresh": "true"},
    )
    assert r.status_code == 422
    body = r.json()
    assert body["code"] == "FRESH_NOT_SUPPORTED_FOR_VENUE"
    assert body["details"]["venue"] == venue
    assert "fresh=false" in body["details"]["hint"]


# ─── 未注册 venue ──────────────────────────────────────────────────


def test_ticker_fresh_true_unknown_venue_lists_supported(
    ticker_client: TestClient, auth_headers: dict[str, str]
) -> None:
    """未注册 venue → 400 VALIDATION_ERROR + details.supported 列出已注册（与 /backfill/bars 一致）。"""
    r = ticker_client.get(
        "/ticker",
        headers=auth_headers,
        params={"venue": "bitfinex", "symbol": "BTC/USDT", "fresh": "true"},
    )
    assert r.status_code == 400
    body = r.json()
    assert body["code"] == "VALIDATION_ERROR"
    assert "bitfinex" in body["message"]
    supported = body["details"]["supported"]
    for v in ("binance", "yfinance", "alpaca", "akshare", "fred"):
        assert v in supported


# ─── fresh=false 仍走 DB cache，不动 connector ────────────────────


def test_ticker_fresh_false_still_returns_404_when_no_db_data(
    ticker_client: TestClient, auth_headers: dict[str, str]
) -> None:
    """fresh=false（默认）走 DB；DB 无该 symbol → 404 NO_PRICE_AVAILABLE（不调任何 connector）。

    回归：connector 改造不该影响 fresh=false 的 DB-only 路径。
    """
    r = ticker_client.get(
        "/ticker",
        headers=auth_headers,
        params={"venue": "akshare", "symbol": f"GHOST-{uuid4().hex[:8]}"},
    )
    assert r.status_code == 404
    assert r.json()["code"] == "NO_PRICE_AVAILABLE"
