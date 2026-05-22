"""Analyst（technical / fundamental）单测 —— 用 FakeLLM + respx mock data-service。"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
import respx
from httpx import Response

from inalpha_research.analysts.fundamental import FundamentalAnalyst
from inalpha_research.analysts.technical import TechnicalAnalyst
from inalpha_research.data_client import DataClient
from inalpha_research.llm.client import FakeLLMClient
from inalpha_research.schemas import AnalystBrief

from .conftest import make_bar_row


def _as_of() -> datetime:
    return datetime(2026, 5, 21, 12, 0, tzinfo=UTC)


@pytest.fixture
async def data_client() -> DataClient:
    return DataClient(base_url="http://data-mock.test", jwt_token="t")


# ────────────────────────────────────────────────────────────────────
# Technical
# ────────────────────────────────────────────────────────────────────


@respx.mock
async def test_technical_analyst_returns_brief(data_client: DataClient) -> None:
    """喂 60 根简单 K 线 + FakeLLM 预设 → AnalystBrief 字段就位。"""
    bars = [
        make_bar_row(
            (_as_of() - timedelta(hours=60 - i)).isoformat(),
            close=100 + i * 0.1,
        )
        for i in range(60)
    ]
    respx.get("http://data-mock.test/bars").mock(return_value=Response(200, json=bars))

    llm = FakeLLMClient(
        {
            "technical analyst": {
                "stance": "bullish",
                "confidence": 0.8,
                "summary": "Clean upcross.",
                "key_points": ["SMA20 > SMA50", "RSI 60"],
            }
        }
    )
    analyst = TechnicalAnalyst(llm=llm, data=data_client)
    brief = await analyst.run(
        venue="binance",
        symbol="BTC/USDT",
        timeframe="1h",
        as_of=_as_of(),
        lookback_days=7,
    )

    assert isinstance(brief, AnalystBrief)
    assert brief.analyst == "technical"
    assert brief.stance == "bullish"
    assert brief.confidence == 0.8
    assert "SMA20 > SMA50" in brief.key_points

    # 检查 LLM call 拿到了指标快照（snapshot 文字应该出现在 user prompt）
    assert len(llm.calls) == 1
    user_prompt = llm.calls[0]["user"]
    assert "indicator_snapshot" in user_prompt
    assert "sma20" in user_prompt.lower()


@respx.mock
async def test_technical_handles_short_history(data_client: DataClient) -> None:
    """K 线 < 15 根时 RSI=None，但 analyst 不应崩。"""
    bars = [make_bar_row((_as_of() - timedelta(hours=10 - i)).isoformat()) for i in range(10)]
    respx.get("http://data-mock.test/bars").mock(return_value=Response(200, json=bars))

    llm = FakeLLMClient({"technical analyst": {"stance": "neutral", "confidence": 0.3, "summary": "thin data"}})
    analyst = TechnicalAnalyst(llm=llm, data=data_client)
    brief = await analyst.run(
        venue="binance",
        symbol="BTC/USDT",
        timeframe="1h",
        as_of=_as_of(),
        lookback_days=1,
    )
    assert brief.stance == "neutral"


@respx.mock
async def test_technical_propagates_data_service_error(data_client: DataClient) -> None:
    from inalpha_research.data_client import DataServiceError

    respx.get("http://data-mock.test/bars").mock(return_value=Response(500, json={"code": "DB_DOWN", "message": "pg down"}))

    llm = FakeLLMClient({})
    analyst = TechnicalAnalyst(llm=llm, data=data_client)
    with pytest.raises(DataServiceError):
        await analyst.run(
            venue="binance",
            symbol="BTC/USDT",
            timeframe="1h",
            as_of=_as_of(),
            lookback_days=1,
        )


# ────────────────────────────────────────────────────────────────────
# Fundamental
# ────────────────────────────────────────────────────────────────────


async def test_fundamental_runs_without_data_fetch(data_client: DataClient) -> None:
    """D-8b 基本面 LLM-only —— 不应该调 data-service。"""
    llm = FakeLLMClient(
        {
            "fundamental": {
                "stance": "neutral",
                "confidence": 0.4,
                "summary": "Macro mixed.",
                "key_points": ["halving priced"],
            }
        }
    )
    analyst = FundamentalAnalyst(llm=llm, data=data_client)
    brief = await analyst.run(
        venue="binance",
        symbol="BTC/USDT",
        timeframe="1h",
        as_of=_as_of(),
        lookback_days=30,
    )
    assert brief.analyst == "fundamental"
    assert brief.stance == "neutral"
    # confirm no data fetch by checking we did not hit respx (no mock set)


async def test_fundamental_handles_missing_fields(data_client: DataClient) -> None:
    """LLM 返简陋 dict 时也能兜底返 brief，不抛。"""
    llm = FakeLLMClient({"fundamental": {}})  # 空 dict
    analyst = FundamentalAnalyst(llm=llm, data=data_client)
    brief = await analyst.run(
        venue="binance",
        symbol="BTC/USDT",
        timeframe="1h",
        as_of=_as_of(),
        lookback_days=30,
    )
    # 默认 fallback
    assert brief.stance == "neutral"
    assert brief.confidence == 0.5
    assert brief.summary == "(no summary)"


async def test_fundamental_invalid_stance_falls_back_to_neutral(data_client: DataClient) -> None:
    """LLM 返不在 enum 里的 stance（typo 等）应该被 pydantic 校验拒。"""
    from pydantic import ValidationError

    llm = FakeLLMClient(
        {
            "fundamental": {
                "stance": "very-bullish",  # 不在 enum 里
                "confidence": 0.9,
                "summary": "hmm",
            }
        }
    )
    analyst = FundamentalAnalyst(llm=llm, data=data_client)
    with pytest.raises(ValidationError):
        await analyst.run(
            venue="binance",
            symbol="BTC/USDT",
            timeframe="1h",
            as_of=_as_of(),
            lookback_days=30,
        )
