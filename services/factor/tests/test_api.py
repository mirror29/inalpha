"""API 端到端：health / catalog 真跑；score / snapshot 用 fake engine 注入合成数据。"""
from __future__ import annotations

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from inalpha_factor.config import get_factor_settings
from inalpha_factor.deps import get_engine
from inalpha_factor.engine import FactorEngine
from inalpha_factor.main import app

from .conftest import make_ohlcv


class _FakeEngine(FactorEngine):
    """跳过真实 data-service，_fetch_df 直接返回合成 OHLCV。"""

    def __init__(self, df: pd.DataFrame) -> None:
        super().__init__(get_factor_settings())
        self._df = df

    async def _fetch_df(self, **_kwargs: object) -> pd.DataFrame:  # type: ignore[override]
        return self._df


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def test_health(client: TestClient) -> None:
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["service"] == "factor"
    assert "pandas_ta" in body["adapters"]
    assert body["adapters"]["pandas_ta"] is True
    assert body["qlib_enabled"] is False


def test_catalog(client: TestClient) -> None:
    r = client.get("/catalog")
    assert r.status_code == 200
    body = r.json()
    ids = {f["factor_id"] for f in body["factors"]}
    assert "pandas_ta.rsi_14" in ids
    assert "alpha101.a101" in ids
    # qlib 因子露出但 available=false
    qlib = [f for f in body["factors"] if f["source"] == "qlib_alpha158"]
    assert qlib and all(f["available"] is False for f in qlib)


def test_score_with_fake_data() -> None:
    app.dependency_overrides[get_engine] = lambda: _FakeEngine(make_ohlcv(400))
    try:
        client = TestClient(app)
        r = client.post(
            "/score",
            json={"symbol": "BTC/USDT", "timeframe": "1h", "horizon_bars": 5, "lookback_bars": 300},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["bars_used"] > 0
        assert len(body["factors"]) > 0
        f0 = body["factors"][0]
        assert {"factor_id", "rank_ic", "direction", "strength", "low_confidence"} <= set(f0)
    finally:
        app.dependency_overrides.clear()


def test_snapshot_returns_top_n() -> None:
    app.dependency_overrides[get_engine] = lambda: _FakeEngine(make_ohlcv(400))
    try:
        client = TestClient(app)
        r = client.post(
            "/snapshot",
            json={"symbol": "BTC/USDT", "timeframe": "1h", "top_n": 5, "lookback_bars": 300},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["available"] is True
        assert len(body["top_factors"]) <= 5
        # 按 |rank_ic| 降序
        ics = [abs(f["rank_ic"]) for f in body["top_factors"]]
        assert ics == sorted(ics, reverse=True)
    finally:
        app.dependency_overrides.clear()


def test_snapshot_empty_when_no_bars() -> None:
    app.dependency_overrides[get_engine] = lambda: _FakeEngine(
        pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    )
    try:
        client = TestClient(app)
        r = client.post("/snapshot", json={"symbol": "BTC/USDT", "timeframe": "1h"})
        assert r.status_code == 200
        body = r.json()
        assert body["available"] is False
        assert body["top_factors"] == []
    finally:
        app.dependency_overrides.clear()
