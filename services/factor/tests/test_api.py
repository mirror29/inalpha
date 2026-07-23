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
    # ADR-0043 D1：qlib 风格因子纯 pandas 本地算，默认启用
    assert body["qlib_enabled"] is True
    assert body["adapters"]["qlib_alpha158"] is True


def test_retired_backtest_score_route_is_unreachable(client: TestClient) -> None:
    response = client.post("/backtest/score", json={})
    assert response.status_code == 404


def test_catalog(client: TestClient) -> None:
    r = client.get("/catalog")
    assert r.status_code == 200
    body = r.json()
    ids = {f["factor_id"] for f in body["factors"]}
    assert "pandas_ta.rsi_14" in ids
    assert "alpha101.a101" in ids
    # qlib 风格因子默认可用（ADR-0043 D1/D2：纯 pandas，扩容到 30）
    qlib = [f for f in body["factors"] if f["source"] == "qlib_alpha158"]
    assert len(qlib) >= 30 and all(f["available"] is True for f in qlib)


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
        # ADR-0043 D4：选择透明化 + 新有效性字段
        assert body["candidates_evaluated"] >= 50  # 三源扩容后候选 ≥ 50
        assert body["low_confidence_count"] >= 0
        # 选择效应基准（D4 延伸）：候选 ≥50、样本有限 → 噪声地板必然为正
        assert body["ic_null_benchmark"] > 0
        for f in body["top_factors"]:
            assert {"rank_ic_recent", "turnover", "corr_pruned", "decay_state"} <= set(f)
    finally:
        app.dependency_overrides.clear()


def test_snapshot_decorrelates_top_n() -> None:
    """ADR-0043 D3：top-N 内任意两因子时序 |spearman| < 阈值（同质因子被挤掉）。"""
    from itertools import combinations

    from inalpha_factor.engine import _abs_spearman

    df = make_ohlcv(400)
    fake = _FakeEngine(df)
    app.dependency_overrides[get_engine] = lambda: fake
    try:
        client = TestClient(app)
        r = client.post(
            "/snapshot",
            json={"symbol": "BTC/USDT", "timeframe": "1h", "top_n": 8, "lookback_bars": 300},
        )
        assert r.status_code == 200, r.text
        top = r.json()["top_factors"]
        assert len(top) >= 2
        series = fake.compute_on_df(df, None)
        threshold = get_factor_settings().snapshot_corr_threshold
        for a, b in combinations(top, 2):
            corr = _abs_spearman(series.get(a["factor_id"]), series.get(b["factor_id"]))
            assert corr is None or corr < threshold, (
                f"{a['factor_id']} vs {b['factor_id']} corr={corr}"
            )
    finally:
        app.dependency_overrides.clear()


class _PerSymbolEngine(FactorEngine):
    """每个 symbol 返回不同合成数据（横截面要标的间有区分度）。"""

    def __init__(self) -> None:
        super().__init__(get_factor_settings())

    async def _fetch_df(self, *, symbol: str, **_kw: object) -> pd.DataFrame:  # type: ignore[override]
        seed = sum(ord(ch) for ch in symbol)
        return make_ohlcv(300, seed=seed)


def test_panel_score_endpoint() -> None:
    """POST /panel/score 端到端：横截面因子结果 + non-PIT 标注 + 选标的排名。"""
    app.dependency_overrides[get_engine] = lambda: _PerSymbolEngine()
    try:
        client = TestClient(app)
        r = client.post(
            "/panel/score",
            json={
                "symbols": ["AAA", "BBB", "CCC", "DDD", "EEE"],
                "timeframe": "1d",
                "lookback_bars": 200,
                "horizon_bars": 5,
            },
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["is_pit"] is False  # 非 PIT 显式标注
        assert "non-PIT" in body["universe_note"]
        assert len(body["factors"]) > 0
        f0 = body["factors"][0]
        assert f0["ic_kind"] == "cross_sectional"
        assert {"cross_sectional_ic", "n_periods", "latest_ranking"} <= set(f0)
        assert 0 < len(f0["latest_ranking"]) <= 5
        # macro 不参与横截面
        assert not any(f["factor_id"].startswith("macro.") for f in body["factors"])
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
