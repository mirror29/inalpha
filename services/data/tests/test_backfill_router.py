"""``/backfill/bars`` 多 venue 路由 / timeframe 校验测试。

D-9 ``data.backfill_bars`` 工具层解锁后，TS schema 不再卡 venue —— 路由责任完全落到
``api/backfill.py`` 的注册表 + ``_VENUE_TIMEFRAME_SECONDS`` 表。本测试覆盖：

- 5 venue 各自路由到正确 connector，写库行数正确
- venue 不在注册表 → 422 + ``details.supported`` 含已注册列表
- venue 支持但 timeframe 不支持（如 akshare 传 1m）→ 422 +
  ``details.supported_timeframes`` 列出该 venue 允许的 timeframe

**不**走真实 yfinance / akshare / alpaca / fred 网络 —— 这些是 connector 层职责（已在
``test_connectors.py`` 覆盖）；本文件只验 router/registry 装配是否正确。
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.integration


class _FakeConnector:
    """通用 fake connector —— 任意 venue/symbol/timeframe 都返 3 根递增 bar。

    用于验证 ``api/backfill.py`` 的"按 venue 查 registry → 调 fetch_bars → 写库"路径，
    与真实交易所行为解耦。
    """

    async def fetch_bars(
        self,
        symbol: str,
        timeframe: str,
        since: datetime,
        limit: int = 1000,
    ) -> list[tuple[datetime, float, float, float, float, float]]:
        start = since.replace(microsecond=0)
        if start.tzinfo is None:
            start = start.replace(tzinfo=UTC)
        # 时间步长按 timeframe 粗略给一个，避免下次循环 cursor 越界产生死循环
        step = timedelta(days=1) if "d" in timeframe or "w" in timeframe or "mo" in timeframe else timedelta(hours=1)
        return [
            (start + step * i, 100.0 + i, 101.0 + i, 99.0 + i, 100.5 + i, 1000.0 + i)
            for i in range(3)
        ]

    async def close(self) -> None:
        pass


@pytest.fixture
async def app_with_all_venues_mocked() -> AsyncIterator[Any]:
    """启 app + 把 5 venue 的 registry 全部替换成 _FakeConnector。

    复用 conftest.app_with_overrides 的 lifespan 模式，但额外覆盖 alpaca / akshare /
    yfinance / fred —— conftest 默认只 mock 了 binance + test-venue。
    """
    from inalpha_data.connectors import _base as _connectors_base
    from inalpha_data.main import app

    async with app.router.lifespan_context(app):
        for venue in ("binance", "alpaca", "akshare", "yfinance", "fred"):
            _connectors_base._REGISTRY[venue] = _FakeConnector()
        yield app

    app.dependency_overrides.clear()


@pytest.fixture
def all_venues_client(app_with_all_venues_mocked: Any) -> TestClient:
    return TestClient(app_with_all_venues_mocked)


# ─── 5 venue 路由 round-trip ──────────────────────────────────────────


@pytest.mark.parametrize(
    "venue, symbol, timeframe",
    [
        ("binance", "BTC/USDT", "1h"),
        ("alpaca", "AAPL", "1h"),
        ("akshare", "sh.600519", "1d"),
        ("yfinance", "TSLA", "1d"),
        ("fred", "DFF", "1d"),
    ],
)
def test_backfill_routes_each_venue_to_its_connector(
    all_venues_client: TestClient,
    auth_headers: dict[str, str],
    venue: str,
    symbol: str,
    timeframe: str,
) -> None:
    """5 venue × 各自 symbol 形式 → /backfill/bars 200，写库行数 ≥ 3（_FakeConnector 返 3 根）。

    回归用户报"TSLA 报错只支持 Binance"的 root cause —— TS schema 解锁后，TSLA + yfinance
    + 1d 必须能通到底层 connector。
    """
    unique_symbol = f"{symbol}-{uuid4().hex[:8]}"
    # 1d / 1h 跨度都用 3 天 → 不会触发 50k bars 上限
    from_ts = "2026-04-01T00:00:00Z"
    to_ts = "2026-04-04T00:00:00Z"

    r = all_venues_client.post(
        "/backfill/bars",
        headers=auth_headers,
        json={
            "venue": venue,
            "symbol": unique_symbol,
            "timeframe": timeframe,
            "from_ts": from_ts,
            "to_ts": to_ts,
        },
    )
    assert r.status_code == 200, r.json()
    body = r.json()
    assert body["venue"] == venue
    assert body["symbol"] == unique_symbol
    assert body["timeframe"] == timeframe
    assert body["bars_fetched"] >= 3


# ─── 错误路径 ────────────────────────────────────────────────────────


def test_backfill_unknown_venue_returns_supported_list(
    all_venues_client: TestClient, auth_headers: dict[str, str]
) -> None:
    """未注册 venue → 422 VALIDATION_ERROR + details.supported 列出已注册。"""
    r = all_venues_client.post(
        "/backfill/bars",
        headers=auth_headers,
        json={
            "venue": "bitfinex",
            "symbol": "BTC/USDT",
            "timeframe": "1h",
            "from_ts": "2026-04-01T00:00:00Z",
            "to_ts": "2026-04-02T00:00:00Z",
        },
    )
    assert r.status_code == 400
    body = r.json()
    assert body["code"] == "VALIDATION_ERROR"
    assert "bitfinex" in body["message"]
    supported = body["details"]["supported"]
    assert isinstance(supported, list)
    # 5 venue 全在
    for v in ("binance", "alpaca", "akshare", "yfinance", "fred"):
        assert v in supported


@pytest.mark.parametrize(
    "venue, bad_timeframe",
    [
        ("akshare", "1m"),  # akshare 仅日级
        ("akshare", "1h"),  # akshare 仅日级
        ("fred", "1m"),  # fred 仅日级及以上
        ("fred", "1h"),
    ],
)
def test_backfill_rejects_timeframe_unsupported_by_venue(
    all_venues_client: TestClient,
    auth_headers: dict[str, str],
    venue: str,
    bad_timeframe: str,
) -> None:
    """venue 支持但 timeframe 越界 → 422 + supported_timeframes 提示。

    覆盖 api/backfill.py:70-78 的 _VENUE_TIMEFRAME_SECONDS 校验。
    """
    r = all_venues_client.post(
        "/backfill/bars",
        headers=auth_headers,
        json={
            "venue": venue,
            "symbol": f"X-{uuid4().hex[:8]}",
            "timeframe": bad_timeframe,
            "from_ts": "2026-04-01T00:00:00Z",
            "to_ts": "2026-04-02T00:00:00Z",
        },
    )
    assert r.status_code == 400
    body = r.json()
    assert body["code"] == "VALIDATION_ERROR"
    assert "does not support timeframe" in body["message"]
    supported_tfs = body["details"]["supported_timeframes"]
    assert isinstance(supported_tfs, list)
    assert bad_timeframe not in supported_tfs
    assert "1d" in supported_tfs  # 两个 venue 至少都支持 1d
