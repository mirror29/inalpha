"""``POST /custom/score`` 端到端（D-12 · 因子发现 L1）：求值/对照/审计拒绝/降级。"""
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
    def __init__(self, df: pd.DataFrame) -> None:
        super().__init__(get_factor_settings())
        self._df = df

    async def _fetch_df(self, **_kwargs: object) -> pd.DataFrame:  # type: ignore[override]
        return self._df


@pytest.fixture
def fake_client() -> TestClient:
    app.dependency_overrides[get_engine] = lambda: _FakeEngine(make_ohlcv(400))
    yield TestClient(app)
    app.dependency_overrides.clear()


def _post(client: TestClient, expression: str, **extra: object) -> object:
    return client.post(
        "/custom/score",
        json={
            "expression": expression,
            "symbol": "BTC/USDT",
            "timeframe": "1h",
            "lookback_bars": 300,
            "horizon_bars": 5,
            **extra,
        },
    )


def test_custom_score_full_payload(fake_client: TestClient) -> None:
    """合法表达式：effectiveness + p 值 + 库相关性一次出全。"""
    r = _post(fake_client, "($close - Ref($close, 5)) / Ref($close, 5)")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["available"] is True
    f = body["factor"]
    assert f["factor_id"].startswith("custom.")
    assert f["source"] == "custom"
    assert {"rank_ic", "rank_ic_recent", "icir", "turnover", "decay_state"} <= set(f)
    assert 0.0 <= body["ic_pvalue"] <= 1.0
    assert len(body["top_correlated"]) > 0
    assert body["max_corr"] is not None


def test_custom_roc5_redundant_with_qlib_roc5(fake_client: TestClient) -> None:
    """ROC5 表达式与库内 qlib.roc_5 完全同构 → 必须被查重标记。"""
    r = _post(fake_client, "($close - Ref($close, 5)) / Ref($close, 5)")
    body = r.json()
    top_ids = [c["factor_id"] for c in body["top_correlated"]]
    assert any("roc" in fid for fid in top_ids), top_ids
    assert body["max_corr"] > 0.99
    assert body["is_likely_redundant"] is True


def test_custom_lookahead_expression_rejected(fake_client: TestClient) -> None:
    """偷未来的表达式（负 lag）→ 400 FACTOR_EXPRESSION_INVALID，带改写依据。"""
    r = _post(fake_client, "Ref($close, -3) / $close")
    assert r.status_code == 400, r.text
    body = r.json()
    assert body["code"] == "FACTOR_EXPRESSION_INVALID"
    assert "lag" in body["message"]


def test_custom_unknown_operator_rejected(fake_client: TestClient) -> None:
    r = _post(fake_client, "Exec($close, 5)")
    assert r.status_code == 400
    assert r.json()["code"] == "FACTOR_EXPRESSION_INVALID"


def test_custom_no_bars_degrades() -> None:
    """data 无数据：available=false + reason，不 5xx。"""
    app.dependency_overrides[get_engine] = lambda: _FakeEngine(
        pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    )
    try:
        client = TestClient(app)
        r = _post(client, "Mean($close, 20)")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["available"] is False
        assert body["reason"]
        assert body["factor"] is None
    finally:
        app.dependency_overrides.clear()
