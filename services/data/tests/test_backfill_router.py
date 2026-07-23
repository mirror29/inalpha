"""``/backfill/bars`` 多 venue 路由 / timeframe 校验测试。

D-9 ``data.backfill_bars`` 工具层解锁后，TS schema 不再卡 venue —— 路由责任完全落到
``api/backfill.py`` 的注册表 + ``_VENUE_TIMEFRAME_SECONDS`` 表。本测试覆盖：

- 5 venue 各自路由到正确 connector，写库行数正确
- venue 不在注册表 → 422 + ``details.supported`` 含已注册列表
- venue 支持但 timeframe 不支持（如 baostock 传 1m）→ 422 +
  ``details.supported_timeframes`` 列出该 venue 允许的 timeframe

**不**走真实 yfinance / baostock / alpaca / fred 网络 —— 这些是 connector 层职责（已在
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
        step = (
            timedelta(days=1)
            if "d" in timeframe or "w" in timeframe or "mo" in timeframe
            else timedelta(hours=1)
        )
        return [
            (start + step * i, 100.0 + i, 101.0 + i, 99.0 + i, 100.5 + i, 1000.0 + i)
            for i in range(3)
        ]

    async def close(self) -> None:
        pass


@pytest.fixture
async def app_with_all_venues_mocked() -> AsyncIterator[Any]:
    """启 app + 把 5 venue 的 registry 全部替换成 _FakeConnector。

    复用 conftest.app_with_overrides 的 lifespan 模式，但额外覆盖 alpaca / baostock /
    yfinance / fred —— conftest 默认只 mock 了 binance + test-venue。
    """
    from inalpha_data.connectors import _base as _connectors_base
    from inalpha_data.main import app

    async with app.router.lifespan_context(app):
        for venue in ("binance", "alpaca", "baostock", "yfinance", "fred"):
            _connectors_base._REGISTRY[venue] = _FakeConnector()
        yield app

    app.dependency_overrides.clear()


@pytest.fixture
def all_venues_client(app_with_all_venues_mocked: Any) -> TestClient:
    return TestClient(app_with_all_venues_mocked)


class _FailingConnector:
    """模拟外部行情源网络故障。"""

    async def fetch_bars(
        self,
        symbol: str,
        timeframe: str,
        since: datetime,
        limit: int = 1000,
    ) -> list[tuple[datetime, float, float, float, float, float]]:
        raise RuntimeError("upstream timeout")


@pytest.fixture
async def app_with_failing_baostock() -> AsyncIterator[Any]:
    from inalpha_data.connectors import _base as _connectors_base
    from inalpha_data.main import app

    async with app.router.lifespan_context(app):
        _connectors_base._REGISTRY["baostock"] = _FailingConnector()
        yield app
    app.dependency_overrides.clear()


def test_backfill_upstream_failure_returns_502(
    app_with_failing_baostock: Any, auth_headers: dict[str, str]
) -> None:
    """上游网络失败必须显式 502，不能 200 + bars_fetched=0。"""
    response = TestClient(app_with_failing_baostock).post(
        "/backfill/bars",
        headers=auth_headers,
        json={
            "venue": "baostock",
            "symbol": "sh.600518",
            "timeframe": "1d",
            "from_ts": "2026-04-01T00:00:00Z",
            "to_ts": "2026-04-04T00:00:00Z",
        },
    )

    assert response.status_code == 502
    assert response.json()["code"] == "BARS_UPSTREAM_UNAVAILABLE"


# ─── 5 venue 路由 round-trip ──────────────────────────────────────────


@pytest.mark.parametrize(
    "venue, symbol, timeframe",
    [
        ("binance", "BTC/USDT", "1h"),
        ("alpaca", "AAPL", "1h"),
        ("baostock", "sh.600519", "1d"),
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
    unique_symbol = (
        f"sh.{uuid4().int % 1_000_000:06d}"
        if venue == "baostock"
        else f"{symbol}-{uuid4().hex[:8]}"
    )
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
    for v in ("binance", "alpaca", "baostock", "yfinance", "fred"):
        assert v in supported


@pytest.mark.parametrize(
    "venue, bad_timeframe",
    [
        ("baostock", "1m"),  # baostock 不支持 1 分钟
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


def test_legacy_akshare_alias_normalizes_symbol_and_applies_minute_cap(
    all_venues_client: TestClient, auth_headers: dict[str, str]
) -> None:
    """旧 venue + Yahoo 后缀 symbol 必须统一写入 canonical Baostock identity。"""
    symbol = "600518.SH"
    r = all_venues_client.post(
        "/backfill/bars",
        headers=auth_headers,
        json={
            "venue": "akshare",
            "symbol": symbol,
            "timeframe": "5m",
            "from_ts": "2026-01-01T00:00:00Z",
            "to_ts": "2026-04-01T00:00:00Z",
        },
    )
    assert r.status_code == 200, r.json()
    assert r.json()["from_ts"] == "2026-03-25T00:00:00Z"

    from inalpha_shared.db import get_conn

    from inalpha_data.storage.bars import count_bars

    async def _count() -> tuple[int, int]:
        async with get_conn() as conn:
            canonical = await count_bars(conn, "baostock", "sh.600518", "5m")
            legacy = await count_bars(conn, "akshare", symbol, "5m")
            return canonical, legacy

    import asyncio

    canonical_count, legacy_count = asyncio.run(_count())
    assert canonical_count > 0
    assert legacy_count == 0


# ─── 增量 backfill：已缓存则从 max(ts) 续拉，不从 from_ts 全量重拉 ──────────


class _RecordingConnector:
    """记录每次 fetch_bars 收到的 since；返 3 根 hourly 递增 bar（从 since 起）。"""

    def __init__(self) -> None:
        self.seen_since: list[datetime] = []

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
        self.seen_since.append(start)
        return [
            (start + timedelta(hours=i), 100.0 + i, 101.0 + i, 99.0 + i, 100.5 + i, 1000.0 + i)
            for i in range(3)
        ]

    async def close(self) -> None:
        pass


@pytest.fixture
async def app_with_recording_binance() -> AsyncIterator[tuple[Any, _RecordingConnector]]:
    from inalpha_data.connectors import _base as _connectors_base
    from inalpha_data.main import app

    rec = _RecordingConnector()
    async with app.router.lifespan_context(app):
        _connectors_base._REGISTRY["binance"] = rec
        yield app, rec
    app.dependency_overrides.clear()


def test_backfill_second_call_is_incremental_from_cached_max(
    app_with_recording_binance: tuple[Any, _RecordingConnector],
    auth_headers: dict[str, str],
) -> None:
    """二次 backfill 同窗口 → cursor 从已缓存 max(ts) 续拉，不再从 from_ts 全量重拉。

    回归"有缓存却每次重 backfill → 超时"：第二次 fetch_bars 首个 since 应 >= 第一次落库的
    最新 bar ts（而非回到 from_ts）。
    """
    client = TestClient(app_with_recording_binance[0])
    rec = app_with_recording_binance[1]
    symbol = f"BTC/USDT-{uuid4().hex[:8]}"
    base = {"venue": "binance", "symbol": symbol, "timeframe": "1h"}

    # 第一次：窄窗口 [00:00, 02:00]，空缓存 → 从 from_ts 全量；_FakeConnector 落 00/01/02 三根
    r1 = client.post(
        "/backfill/bars",
        headers=auth_headers,
        json={**base, "from_ts": "2026-04-01T00:00:00Z", "to_ts": "2026-04-01T02:00:00Z"},
    )
    assert r1.status_code == 200, r1.json()
    assert rec.seen_since[0] == datetime(2026, 4, 1, 0, 0, tzinfo=UTC)  # 空缓存从头

    # 第二次：更宽窗口 [00:00, 08:00]，缓存已覆盖到 02:00 → cursor 从 02:00 续拉缺口，
    # **绝不回到 from_ts(00:00) 重拉**（这正是修复前超时的根因）。
    rec.seen_since.clear()
    r2 = client.post(
        "/backfill/bars",
        headers=auth_headers,
        json={**base, "from_ts": "2026-04-01T00:00:00Z", "to_ts": "2026-04-01T08:00:00Z"},
    )
    assert r2.status_code == 200, r2.json()
    assert rec.seen_since, "第二次须续拉尾部缺口（02:00→08:00）保新鲜"
    # 首个 since 从已缓存 max(ts)=02:00 起，而非 from_ts=00:00
    assert rec.seen_since[0] == datetime(2026, 4, 1, 2, 0, tzinfo=UTC)
