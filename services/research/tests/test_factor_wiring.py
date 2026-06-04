"""technical analyst × factor-service 接线（docs/miro/11 M3）。

覆盖：
- factor client 提供时，effective_factors 块进 user prompt，优先于 indicator_snapshot
- factor-service 报错 / 无 client 时降级回旧指标快照，不阻断
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import respx
from httpx import Response

from inalpha_research.analysts.technical import TechnicalAnalyst
from inalpha_research.data_client import DataClient
from inalpha_research.factor_client import FactorClient
from inalpha_research.llm.client import FakeLLMClient

from .conftest import make_bar_row


def _as_of() -> datetime:
    return datetime(2026, 5, 21, 12, 0, tzinfo=UTC)


def _brief() -> dict[str, Any]:
    return {"stance": "neutral", "confidence": 0.5, "summary": "t", "key_points": ["kp"]}


def _bars() -> list[dict[str, Any]]:
    return [
        make_bar_row((_as_of() - timedelta(hours=20 - i)).isoformat(), close=100 + i)
        for i in range(20)
    ]


@respx.mock
async def test_effective_factors_injected_into_prompt() -> None:
    respx.get("http://data-mock.test/bars").mock(return_value=Response(200, json=_bars()))
    respx.post("http://factor-mock.test/snapshot").mock(
        return_value=Response(
            200,
            json={
                "venue": "binance",
                "symbol": "BTC/USDT",
                "timeframe": "1h",
                "as_of": _as_of().isoformat(),
                "horizon_bars": 5,
                "bars_used": 700,
                "available": True,
                "reason": None,
                "top_factors": [
                    {
                        "factor_id": "pandas_ta.macd_hist",
                        "source": "pandas_ta",
                        "name": "MACD 柱",
                        "kind": "momentum",
                        "value": 0.012,
                        "rank_ic": 0.041,
                        "icir": 0.9,
                        "sample_size": 680,
                        "quantile_returns": [],
                        "long_short_return": 0.02,
                        "direction": 1,
                        "strength": 0.82,
                        "low_confidence": False,
                    }
                ],
            },
        )
    )

    llm = FakeLLMClient({"you are a technical analyst": _brief()})
    async with (
        DataClient("http://data-mock.test", "t") as data,
        FactorClient("http://factor-mock.test", "t") as factor,
    ):
        analyst = TechnicalAnalyst(llm=llm, data=data, factor=factor)
        await analyst.run(
            venue="binance", symbol="BTC/USDT", timeframe="1h", as_of=_as_of(), lookback_days=2
        )

    user_prompt = llm.calls[0]["user"]
    assert "effective_factors" in user_prompt
    assert "MACD 柱" in user_prompt
    assert "rank_ic=0.041" in user_prompt
    assert "dir=1" in user_prompt


@respx.mock
async def test_factor_service_down_degrades_gracefully() -> None:
    respx.get("http://data-mock.test/bars").mock(return_value=Response(200, json=_bars()))
    respx.post("http://factor-mock.test/snapshot").mock(return_value=Response(503, json={}))

    llm = FakeLLMClient({"you are a technical analyst": _brief()})
    async with (
        DataClient("http://data-mock.test", "t") as data,
        FactorClient("http://factor-mock.test", "t") as factor,
    ):
        analyst = TechnicalAnalyst(llm=llm, data=data, factor=factor)
        brief = await analyst.run(
            venue="binance", symbol="BTC/USDT", timeframe="1h", as_of=_as_of(), lookback_days=2
        )

    # 不抛 + 仍产出 brief；prompt 落回指标快照兜底提示
    assert brief.stance == "neutral"
    user_prompt = llm.calls[0]["user"]
    assert "factor library unavailable" in user_prompt
    assert "indicator_snapshot" in user_prompt


@respx.mock
async def test_snapshot_missing_rank_ic_does_not_crash() -> None:
    """CR major fix：snapshot 因子缺 rank_ic / strength（跨版本灰度）不应崩 deep_dive。"""
    respx.get("http://data-mock.test/bars").mock(return_value=Response(200, json=_bars()))
    respx.post("http://factor-mock.test/snapshot").mock(
        return_value=Response(
            200,
            json={
                "available": True,
                "top_factors": [
                    # 故意缺 rank_ic / strength（旧版 schema），且 value 为 null
                    {"name": "legacy_factor", "kind": "momentum", "value": None, "direction": 1}
                ],
            },
        )
    )

    llm = FakeLLMClient({"you are a technical analyst": _brief()})
    async with (
        DataClient("http://data-mock.test", "t") as data,
        FactorClient("http://factor-mock.test", "t") as factor,
    ):
        analyst = TechnicalAnalyst(llm=llm, data=data, factor=factor)
        brief = await analyst.run(
            venue="binance", symbol="BTC/USDT", timeframe="1h", as_of=_as_of(), lookback_days=2
        )

    assert brief.stance == "neutral"  # 不抛
    user_prompt = llm.calls[0]["user"]
    assert "legacy_factor" in user_prompt
    assert "rank_ic=0.000" in user_prompt  # 缺字段兜底成 0.0


@respx.mock
async def test_no_factor_client_uses_indicator_snapshot() -> None:
    respx.get("http://data-mock.test/bars").mock(return_value=Response(200, json=_bars()))

    llm = FakeLLMClient({"you are a technical analyst": _brief()})
    async with DataClient("http://data-mock.test", "t") as data:
        analyst = TechnicalAnalyst(llm=llm, data=data)  # 无 factor
        await analyst.run(
            venue="binance", symbol="BTC/USDT", timeframe="1h", as_of=_as_of(), lookback_days=2
        )

    user_prompt = llm.calls[0]["user"]
    assert "factor library unavailable" in user_prompt
    assert "indicator_snapshot" in user_prompt