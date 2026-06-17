"""财报 point-in-time 截断单测（ADR-0053 阶段 A）。

纯函数测试（合成 DataFrame，免 akshare / 网络）：报告期发布判定 + as_of 过滤。
"""
from __future__ import annotations

from datetime import UTC, datetime

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from inalpha_data.connectors.akshare import (
    FINANCIALS_PUBLISH_LAG_DAYS,
    _flatten_financial_abstract,
    _period_publishable,
)

pytestmark = pytest.mark.anyio


def _as_of(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=UTC)


def test_period_publishable() -> None:
    lag = FINANCIALS_PUBLISH_LAG_DAYS  # 120
    # 20260331 + 120d ≈ 2026-07-29
    assert _period_publishable("20260331", _as_of("2026-08-01"), lag) is True
    assert _period_publishable("20260331", _as_of("2026-05-01"), lag) is False
    # 非法报告期 → False（不抛）
    assert _period_publishable("not-a-date", _as_of("2026-08-01"), lag) is False


def _abstract_df() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "指标": ["净资产收益率", "毛利率"],
            "20251231": [10.0, 50.0],
            "20260331": [12.0, 52.0],
        }
    )


def test_flatten_no_as_of_picks_latest_period() -> None:
    out = _flatten_financial_abstract(_abstract_df())
    assert out["净资产收益率"] == 12.0  # 最新期 20260331
    assert out["毛利率"] == 52.0


def test_flatten_as_of_skips_unpublished_period() -> None:
    # 2026-05-01：20260331 还没披露（+120d=07-29），只能用 20251231（+120d=04-30 已过）
    out = _flatten_financial_abstract(_abstract_df(), as_of=_as_of("2026-05-01"))
    assert out["净资产收益率"] == 10.0
    assert out["毛利率"] == 50.0


def test_flatten_as_of_before_any_publication_returns_empty() -> None:
    # 2026-01-01：连 20251231 都没披露 → 无可用期
    out = _flatten_financial_abstract(_abstract_df(), as_of=_as_of("2026-01-01"))
    assert out == {}


def test_fundamentals_endpoint_threads_as_of(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """GET /fundamentals?as_of=... 把 as_of 透传到 connector。"""
    from inalpha_data.connectors import akshare as ak

    captured: dict[str, str | None] = {}
    original = ak._connector.fetch_financials

    async def mock_fin(symbol, as_of=None):
        captured["as_of"] = as_of
        return {
            "venue": "akshare",
            "symbol": symbol,
            "available": False,
            "reason": f"no financials published as of {as_of}",
        }

    ak._connector.fetch_financials = mock_fin
    try:
        r = client.get(
            "/fundamentals",
            headers=auth_headers,
            params={
                "venue": "akshare",
                "symbol": "sh.600519",
                "as_of": "2026-01-01T00:00:00Z",
            },
        )
        assert r.status_code == 200
        assert captured["as_of"] == "2026-01-01T00:00:00Z"
        assert r.json()["available"] is False
    finally:
        ak._connector.fetch_financials = original
