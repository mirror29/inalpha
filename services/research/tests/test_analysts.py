"""Analyst（5 个）单测 —— 用 FakeLLM + respx mock data-service / FNG。"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
import respx
from httpx import HTTPStatusError, Response

from inalpha_research.analysts.fundamental import FundamentalAnalyst
from inalpha_research.analysts.macro import MacroAnalyst
from inalpha_research.analysts.risk import RiskAnalyst
from inalpha_research.analysts.sentiment import SentimentAnalyst
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
    """LLM 返不在 enum 里的 stance / 越界 confidence 走兜底，不抛（review B2 fix）。

    旧行为：pydantic 抛 ValidationError → 整条 deep_dive 链路 500。
    新行为：stance fallback 到 'neutral'、confidence clamp 到 [0, 1]，brief
    正常返。
    """
    llm = FakeLLMClient(
        {
            "fundamental": {
                "stance": "very-bullish",  # 不在 enum 里
                "confidence": 1.7,         # 越界
                "summary": "hmm",
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
    assert brief.stance == "neutral"  # fallback
    assert brief.confidence == 1.0    # clamp 到上限


async def test_fundamental_negative_confidence_clamps_to_zero(data_client: DataClient) -> None:
    llm = FakeLLMClient(
        {
            "fundamental": {
                "stance": "bearish",
                "confidence": -0.3,
                "summary": "ok",
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
    assert brief.confidence == 0.0
    assert brief.stance == "bearish"


# ────────────────────────────────────────────────────────────────────
# Sentiment
# ────────────────────────────────────────────────────────────────────


def _fng_payload(latest: int = 22, span: int = 30) -> dict:
    """合成 alternative.me FNG 风格响应。"""
    return {
        "data": [
            {
                "value": str(latest if i == 0 else 30 + (i % 15)),
                "value_classification": "Extreme Fear" if i == 0 else "Fear",
                "timestamp": str(1716163200 - i * 86400),
            }
            for i in range(span)
        ]
    }


@respx.mock
async def test_sentiment_returns_brief(data_client: DataClient) -> None:
    """FNG 22（Extreme Fear）→ analyst 返 brief；user prompt 含 FNG 值 + 30d 序列。"""
    respx.get("https://api.alternative.me/fng/").mock(
        return_value=Response(200, json=_fng_payload(latest=22))
    )

    llm = FakeLLMClient(
        {
            "sentiment analyst": {
                "stance": "bullish",
                "confidence": 0.7,
                "summary": "FNG 22 Extreme Fear → contrarian bullish.",
                "key_points": ["FNG=22", "30d avg ~35"],
            }
        }
    )
    analyst = SentimentAnalyst(llm=llm, data=data_client)
    brief = await analyst.run(
        venue="binance",
        symbol="BTC/USDT",
        timeframe="1h",
        as_of=_as_of(),
        lookback_days=7,
    )

    assert isinstance(brief, AnalystBrief)
    assert brief.analyst == "sentiment"
    assert brief.stance == "bullish"

    user_prompt = llm.calls[0]["user"]
    # D-9 重命名：``latest_fng:`` → ``crypto_fng:``，``value:`` → ``latest_value:``
    assert "crypto_fng:" in user_prompt
    assert "latest_value: 22" in user_prompt
    assert "trend_snapshot" in user_prompt
    assert "market_type: crypto" in user_prompt


@respx.mock
async def test_sentiment_propagates_fng_api_error(data_client: DataClient) -> None:
    """FNG API 5xx 让 analyst 抛错（不静默兜底，让 runner._failed_brief 处理）。"""
    respx.get("https://api.alternative.me/fng/").mock(
        return_value=Response(503, json={"error": "down"})
    )

    llm = FakeLLMClient({})
    analyst = SentimentAnalyst(llm=llm, data=data_client)
    with pytest.raises(HTTPStatusError):
        await analyst.run(
            venue="binance",
            symbol="BTC/USDT",
            timeframe="1h",
            as_of=_as_of(),
            lookback_days=7,
        )
    # 失败应发生在 LLM call 之前
    assert llm.calls == []


@respx.mock
async def test_sentiment_rejects_unexpected_payload_shape(data_client: DataClient) -> None:
    """FNG 返回 list 而不是 ``{data: [...]}`` 时应该抛 RuntimeError。"""
    respx.get("https://api.alternative.me/fng/").mock(
        return_value=Response(200, json=[1, 2, 3])  # 非 dict
    )

    llm = FakeLLMClient({})
    analyst = SentimentAnalyst(llm=llm, data=data_client)
    with pytest.raises(RuntimeError, match="unexpected"):
        await analyst.run(
            venue="binance",
            symbol="BTC/USDT",
            timeframe="1h",
            as_of=_as_of(),
            lookback_days=7,
        )


# ────────────────────────────────────────────────────────────────────
# Risk
# ────────────────────────────────────────────────────────────────────


@respx.mock
async def test_risk_returns_brief_with_indicators(data_client: DataClient) -> None:
    """喂 120 根 K 线 → risk snapshot（ATR / max DD / vol z）就位在 user prompt。"""
    # 构造一个温和上涨 + 中间一次回撤的序列，便于 max_dd 非零
    closes = [100.0 + i * 0.2 for i in range(60)] + [110.0 - i * 0.3 for i in range(30)] + [
        101.0 + i * 0.1 for i in range(30)
    ]
    bars = [
        {
            "ts": (_as_of() - timedelta(hours=120 - i)).isoformat(),
            "venue": "binance",
            "symbol": "BTC/USDT",
            "timeframe": "1h",
            "open": c, "high": c + 0.5, "low": c - 0.5, "close": c, "volume": 1.0,
        }
        for i, c in enumerate(closes)
    ]
    respx.get("http://data-mock.test/bars").mock(return_value=Response(200, json=bars))

    llm = FakeLLMClient(
        {
            "risk analyst": {
                "stance": "neutral",
                "confidence": 0.55,
                "summary": "Normal vol; mild DD.",
                "key_points": ["ATR/close ~0.5%", "max_dd ~8%"],
            }
        }
    )
    analyst = RiskAnalyst(llm=llm, data=data_client)
    brief = await analyst.run(
        venue="binance",
        symbol="BTC/USDT",
        timeframe="1h",
        as_of=_as_of(),
        lookback_days=7,
    )

    assert brief.analyst == "risk"
    assert brief.stance == "neutral"

    user_prompt = llm.calls[0]["user"]
    assert "risk_snapshot" in user_prompt
    assert "atr14" in user_prompt
    assert "max_drawdown_pct" in user_prompt
    assert "vol_zscore_14_vs_long" in user_prompt


@respx.mock
async def test_risk_handles_empty_bars(data_client: DataClient) -> None:
    """data 返空 → snapshot 标 available=False，analyst 仍调 LLM 不抛。"""
    respx.get("http://data-mock.test/bars").mock(return_value=Response(200, json=[]))

    llm = FakeLLMClient(
        {
            "risk analyst": {
                "stance": "neutral",
                "confidence": 0.1,
                "summary": "no data",
            }
        }
    )
    analyst = RiskAnalyst(llm=llm, data=data_client)
    brief = await analyst.run(
        venue="binance",
        symbol="BTC/USDT",
        timeframe="1h",
        as_of=_as_of(),
        lookback_days=1,
    )

    assert brief.stance == "neutral"
    user_prompt = llm.calls[0]["user"]
    assert "available" in user_prompt and "False" in user_prompt


# ────────────────────────────────────────────────────────────────────
# Macro
# ────────────────────────────────────────────────────────────────────


async def test_macro_runs_without_data_fetch(data_client: DataClient) -> None:
    """macro analyst 不调 data-service，user prompt 含日历段。"""
    llm = FakeLLMClient(
        {
            "macro analyst": {
                "stance": "neutral",
                "confidence": 0.5,
                "summary": "FOMC in 4 weeks; quiet window.",
                "key_points": ["FOMC mid-June"],
            }
        }
    )
    analyst = MacroAnalyst(llm=llm, data=data_client)
    brief = await analyst.run(
        venue="binance",
        symbol="BTC/USDT",
        timeframe="1h",
        as_of=_as_of(),  # 2026-05-21
        lookback_days=30,
    )

    assert brief.analyst == "macro"
    assert brief.stance == "neutral"

    user_prompt = llm.calls[0]["user"]
    assert "upcoming_macro_events" in user_prompt
    # _as_of() 2026-05-21 的 ±14 天窗口应该包含 FOMC 2026-06-18? 那是 28 天后，不在窗口
    # 但 2026-06-06 NFP（16 天后）也不在。窗口里有 2026-05-13 CPI（8 天前）
    assert "2026-05-13" in user_prompt or "(none in ±14d window)" in user_prompt


async def test_macro_handles_no_events_in_window(data_client: DataClient) -> None:
    """as_of 远离所有硬编码事件 → 提示 '(none in ±14d window)' 但仍跑通。"""
    llm = FakeLLMClient(
        {
            "macro analyst": {
                "stance": "neutral",
                "confidence": 0.3,
                "summary": "no near-term catalysts",
            }
        }
    )
    analyst = MacroAnalyst(llm=llm, data=data_client)
    # 取 2030-01-01，远离所有硬编码事件
    brief = await analyst.run(
        venue="binance",
        symbol="BTC/USDT",
        timeframe="1h",
        as_of=datetime(2030, 1, 1, tzinfo=UTC),
        lookback_days=30,
    )

    assert brief.stance == "neutral"
    user_prompt = llm.calls[0]["user"]
    # D-9：events 拆 past / upcoming 后，空窗口表现为两组分别 "(none)"
    assert "past_macro_events_last_14d: (none)" in user_prompt
    assert "upcoming_macro_events_next_14d: (none)" in user_prompt
