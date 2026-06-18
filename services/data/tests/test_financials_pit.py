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


def test_flatten_as_of_no_indicator_column_does_not_leak() -> None:
    """#100：PIT 模式 + 缺「指标」列(格式异常)→ 返空，绝不走 iloc[-1] 后门泄漏未来列。"""
    # 缺「指标」列的异常表（含未披露报告期 20260331）
    df = pd.DataFrame({"name": ["x"], "20251231": [10.0], "20260331": [12.0]})
    # 非 PIT：维持旧兜底（返 iloc[-1] 全列）
    assert _flatten_financial_abstract(df) != {}
    # PIT：即便 as_of 后有已发布期，缺「指标」列也返空（不泄漏全列）
    assert _flatten_financial_abstract(df, as_of=_as_of("2026-08-01")) == {}


async def test_yfinance_as_of_marks_pit_not_supported(monkeypatch) -> None:
    """#100：yfinance 接 as_of 但不截断 → 响应 reason 写明 PIT 未生效，防调用方误信。"""
    from inalpha_data.connectors import yfinance_conn as yf

    def fake_sync(symbol):
        return {
            "venue": "yfinance",
            "symbol": symbol,
            "available": True,
            "as_of": "2026-06-18T00:00:00Z",  # 取数时刻(now)，非请求 as_of
            "indicators": {"pe_ratio": 30.0},
        }

    monkeypatch.setattr(yf, "_fetch_financials_sync", fake_sync)
    conn = yf.YfinanceConnector()
    # 给 as_of → reason 提示 PIT 未生效
    out = await conn.fetch_financials("AAPL", as_of="2020-01-01T00:00:00Z")
    assert "PIT not supported" in (out.get("reason") or "")
    # 不给 as_of → 不注入 reason（维持原行为）
    out2 = await conn.fetch_financials("AAPL")
    assert "PIT" not in (out2.get("reason") or "")


async def test_financials_cache_is_pit_aware(monkeypatch) -> None:
    """#102 CR：PIT 缓存按 (symbol, as_of 天) 分格——同一天复用、不同天各打一次。"""
    from inalpha_data.connectors import akshare as ak

    calls = {"n": 0}

    def fake_sync(*, prefix, code, as_of=None, publish_lag_days=120):
        calls["n"] += 1
        return {"净资产收益率(ROE)": 18.0}

    monkeypatch.setattr(ak, "_fetch_financials_sync", fake_sync)
    conn = ak.AkshareConnector()  # hk → 跳过 sh/sz 的 Baidu 估值网络调用

    a1 = await conn.fetch_financials("hk.00700", as_of="2020-06-30T00:00:00Z")
    a2 = await conn.fetch_financials("hk.00700", as_of="2020-06-30T23:00:00Z")  # 同一天
    assert a1["available"] is True and a2["available"] is True
    assert calls["n"] == 1  # 同 (symbol, 天) → 第二次命中缓存,不再打 akshare

    await conn.fetch_financials("hk.00700", as_of="2020-09-30T00:00:00Z")  # 另一天
    assert calls["n"] == 2  # 不同天 → 另一格,再打一次


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
