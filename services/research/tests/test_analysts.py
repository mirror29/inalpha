"""Analyst（6 个）单测 —— 用 FakeLLM + respx mock data-service / FNG。"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
import respx
from httpx import Response

from inalpha_research.analysts.fundamental import FundamentalAnalyst
from inalpha_research.analysts.macro import MacroAnalyst
from inalpha_research.analysts.risk import RiskAnalyst
from inalpha_research.analysts.sentiment import SentimentAnalyst
from inalpha_research.analysts.technical import TechnicalAnalyst
from inalpha_research.analysts.valuation import ValuationAnalyst
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


@respx.mock
async def test_fundamental_crypto_skips_fundamentals_fetch(data_client: DataClient) -> None:
    """D-12 路由：crypto 无财报 → 不打 /fundamentals（省 round-trip），prompt 显式说明。"""
    fund_route = respx.get("http://data-mock.test/fundamentals").mock(
        return_value=Response(200, json={"available": True, "indicators": {}})
    )
    respx.get("http://data-mock.test/web/search").mock(
        return_value=Response(200, json={"results": []})
    )
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
    assert not fund_route.called
    user_prompt = llm.calls[0]["user"]
    assert "crypto has no financial statements" in user_prompt


@respx.mock
async def test_fundamental_web_search_year_follows_as_of(data_client: DataClient) -> None:
    """财报查询年份随 as_of 动态拼，不写死（issue #63 回归）。"""
    web_route = respx.get("http://data-mock.test/web/search").mock(
        return_value=Response(200, json={"results": []})
    )
    llm = FakeLLMClient(
        {
            "fundamental": {
                "stance": "neutral",
                "confidence": 0.4,
                "summary": "No data.",
                "key_points": [],
            }
        }
    )
    analyst = FundamentalAnalyst(llm=llm, data=data_client)
    await analyst.run(
        venue="yfinance",
        symbol="AAPL",
        timeframe="1d",
        as_of=datetime(2031, 3, 2, tzinfo=UTC),
        lookback_days=30,
    )
    query = web_route.calls.last.request.url.params["query"]
    assert "2031" in query
    assert "2026" not in query  # 修复前的硬编码年份


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
    新行为：stance fallback 到 'neutral'、confidence clamp 到 [0, 1]；D-12 起
    crypto（无 live 财报）再被双档 cap 压到 0.55。
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
    assert brief.stance == "neutral"   # fallback
    assert brief.confidence == 0.55    # clamp 1.0 后再吃无数据档 cap


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
    """FNG API 5xx → web search fallback → LLM call with empty data → brief returned."""
    respx.get("https://api.alternative.me/fng/").mock(
        return_value=Response(503, json={"error": "down"})
    )
    respx.get("http://data-mock.test/web/search").mock(
        return_value=Response(200, json={"results": []})
    )

    llm = FakeLLMClient(
        {
            "you are a sentiment analyst": {
                "stance": "neutral",
                "confidence": 0.4,
                "summary": "No FNG data available; limited sentiment signal.",
                "key_points": ["FNG unavailable", "no web results"],
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
    assert brief.analyst == "sentiment"


@respx.mock
async def test_sentiment_web_search_year_follows_as_of(data_client: DataClient) -> None:
    """fallback 查询年份随 as_of 动态拼，不写死（issue #63 回归）。"""
    respx.get("https://api.alternative.me/fng/").mock(
        return_value=Response(503, json={"error": "down"})
    )
    web_route = respx.get("http://data-mock.test/web/search").mock(
        return_value=Response(200, json={"results": []})
    )
    llm = FakeLLMClient(
        {
            "you are a sentiment analyst": {
                "stance": "neutral",
                "confidence": 0.4,
                "summary": "No data.",
                "key_points": [],
            }
        }
    )
    analyst = SentimentAnalyst(llm=llm, data=data_client)
    await analyst.run(
        venue="binance",
        symbol="BTC/USDT",
        timeframe="1h",
        as_of=datetime(2031, 3, 2, tzinfo=UTC),
        lookback_days=7,
    )
    query = web_route.calls.last.request.url.params["query"]
    assert "2031" in query
    assert "2026" not in query  # 修复前的硬编码年份


@respx.mock
async def test_sentiment_rejects_unexpected_payload_shape(data_client: DataClient) -> None:
    """FNG returns list instead of dict → web search fallback → LLM → brief."""
    respx.get("https://api.alternative.me/fng/").mock(
        return_value=Response(200, json=[1, 2, 3])  # 非 dict
    )
    respx.get("http://data-mock.test/web/search").mock(
        return_value=Response(200, json={"results": []})
    )

    llm = FakeLLMClient(
        {
            "you are a sentiment analyst": {
                "stance": "neutral",
                "confidence": 0.35,
                "summary": "FNG payload malformed; using limited sentiment data.",
                "key_points": ["FNG parse error", "no web results"],
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
    assert brief.analyst == "sentiment"


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


# ────────────────────────────────────────────────────────────────────
# D-10: Fundamental with real financial data + Sentiment web search
# ────────────────────────────────────────────────────────────────────


@respx.mock
async def test_fundamental_with_financials_data(data_client: DataClient) -> None:
    """D-10: fundamental analyst fetches real financial data and feeds to LLM."""
    # Mock GET /fundamentals to return real-looking data
    respx.get("http://data-mock.test/fundamentals").mock(
        return_value=Response(200, json={
            "venue": "akshare",
            "symbol": "sh.600519",
            "available": True,
            "as_of": "2026-05-29T00:00:00Z",
            "indicators": {
                "market_cap": 2.3e12,
                "pe_ratio": 32.5,
                "roe": 0.283,
                "revenue_yoy": 0.153,
                "profit_yoy": 0.187,
            },
        })
    )
    # Mock GET /web/search to return empty (simplest case)
    respx.get("http://data-mock.test/web/search").mock(
        return_value=Response(200, json={"results": []})
    )

    llm = FakeLLMClient({
        "fundamental": {
            "stance": "bullish",
            "confidence": 0.7,
            "summary": "Strong financials, ROE 28.3%, revenue +15.3%.",
            "key_points": ["ROE high", "revenue growing"],
        }
    })
    analyst = FundamentalAnalyst(llm=llm, data=data_client)
    brief = await analyst.run(
        venue="akshare",
        symbol="sh.600519",
        timeframe="1d",
        as_of=_as_of(),
        lookback_days=180,
    )

    assert brief.analyst == "fundamental"
    assert brief.stance == "bullish"

    # Verify financial data made it into the user prompt
    assert len(llm.calls) == 1
    user_prompt = llm.calls[0]["user"]
    assert "financial_data" in user_prompt
    assert "32.5" in user_prompt  # PE ratio
    assert "28.3%" in user_prompt  # ROE
    assert "15.3%" in user_prompt  # revenue_yoy


@respx.mock
async def test_fundamental_financials_unavailable(data_client: DataClient) -> None:
    """D-10: when financials API returns unavailable, analyst falls back to LLM-only."""
    respx.get("http://data-mock.test/fundamentals").mock(
        return_value=Response(200, json={
            "available": False,
            "reason": "no data for this ticker",
        })
    )
    respx.get("http://data-mock.test/web/search").mock(
        return_value=Response(200, json={"results": []})
    )

    llm = FakeLLMClient({
        "fundamental": {
            "stance": "neutral",
            "confidence": 0.4,
            "summary": "No live data available.",
            "key_points": ["data unavailable"],
        }
    })
    analyst = FundamentalAnalyst(llm=llm, data=data_client)
    brief = await analyst.run(
        venue="akshare",
        symbol="sh.600519",
        timeframe="1d",
        as_of=_as_of(),
        lookback_days=180,
    )

    assert brief.analyst == "fundamental"
    # Should still work (graceful degradation)
    assert brief.stance == "neutral"

    user_prompt = llm.calls[0]["user"]
    assert "not available" in user_prompt.lower()
    assert "lower confidence" in user_prompt.lower()


@respx.mock
async def test_fundamental_confidence_capped_at_075_with_live_data(
    data_client: DataClient,
) -> None:
    """D-12 双档：有 live 财报时 LLM 给 0.9 也被代码级 clamp 到 0.75。"""
    respx.get("http://data-mock.test/fundamentals").mock(
        return_value=Response(
            200,
            json={
                "venue": "yfinance",
                "symbol": "AAPL",
                "available": True,
                "as_of": "2026-05-20T00:00:00Z",
                "indicators": {"pe_ratio": 30.1, "roe": 0.45},
            },
        )
    )
    respx.get("http://data-mock.test/web/search").mock(
        return_value=Response(200, json={"results": []})
    )
    llm = FakeLLMClient(
        {
            "fundamental": {
                "stance": "bullish",
                "confidence": 0.9,  # LLM 不守 prompt cap 的情形
                "summary": "Great numbers.",
            }
        }
    )
    analyst = FundamentalAnalyst(llm=llm, data=data_client)
    brief = await analyst.run(
        venue="alpaca",  # 路由 → yfinance
        symbol="AAPL",
        timeframe="1d",
        as_of=_as_of(),
        lookback_days=30,
    )
    assert brief.confidence == 0.75
    # 财报快照的 as_of 渲染进块头（时效性红线）
    user_prompt = llm.calls[0]["user"]
    assert "data as_of 2026-05-20" in user_prompt


@respx.mock
async def test_fundamental_confidence_capped_at_055_without_live_data(
    data_client: DataClient,
) -> None:
    """D-12 双档：财报 unavailable 时 LLM 给 0.9 被 clamp 到 0.55。"""
    respx.get("http://data-mock.test/fundamentals").mock(
        return_value=Response(200, json={"available": False, "reason": "no data"})
    )
    respx.get("http://data-mock.test/web/search").mock(
        return_value=Response(200, json={"results": []})
    )
    llm = FakeLLMClient(
        {"fundamental": {"stance": "bullish", "confidence": 0.9, "summary": "vibes"}}
    )
    analyst = FundamentalAnalyst(llm=llm, data=data_client)
    brief = await analyst.run(
        venue="yfinance",
        symbol="AAPL",
        timeframe="1d",
        as_of=_as_of(),
        lookback_days=30,
    )
    assert brief.confidence == 0.55


@respx.mock
async def test_valuation_confidence_two_tier_cap(data_client: DataClient) -> None:
    """valuation 同享双档 cap：有指标 0.75 / 无指标 0.55（代码级）。"""
    respx.get("http://data-mock.test/fundamentals").mock(
        return_value=Response(
            200, json={"available": True, "indicators": {"pe_ratio": 12.0}}
        )
    )
    respx.get("http://data-mock.test/web/search").mock(
        return_value=Response(200, json={"results": []})
    )
    llm = FakeLLMClient(
        {
            "you are a relative valuation analyst": {
                "stance": "bullish",
                "confidence": 0.95,
                "summary": "cheap",
            }
        }
    )
    analyst = ValuationAnalyst(llm=llm, data=data_client)
    brief = await analyst.run(
        venue="alpaca",
        symbol="AAPL",
        timeframe="1d",
        as_of=_as_of(),
        lookback_days=30,
    )
    assert brief.confidence == 0.75


@respx.mock
async def test_sentiment_non_crypto_uses_web_search(data_client: DataClient) -> None:
    """D-10: for A-share (non-crypto), sentiment calls get_news AND get_web_search."""
    # Mock GET /news (get_news defaults venue=yfinance)
    respx.get("http://data-mock.test/news").mock(
        return_value=Response(200, json={"venue": "yfinance", "symbol": "sh.600519", "items": []})
    )
    # Mock GET /web/search returning some web results
    respx.get("http://data-mock.test/web/search").mock(
        return_value=Response(200, json={
            "query": "sh.600519 stock news sentiment analysis",
            "backend": "auto",
            "results": [
                {"title": "茅台股价创新高", "url": "https://x.com/1", "snippet": "贵州茅台今日股价大涨..."},
                {"title": "机构看好茅台Q1业绩", "url": "https://x.com/2", "snippet": "多家机构上调茅台目标价..."},
            ],
        })
    )

    llm = FakeLLMClient({
        "sentiment analyst": {
            "stance": "bullish",
            "confidence": 0.65,
            "summary": "Web results show positive sentiment.",
            "key_points": ["positive news flow", "institutional bullish"],
        }
    })
    analyst = SentimentAnalyst(llm=llm, data=data_client)
    brief = await analyst.run(
        venue="akshare",
        symbol="sh.600519",
        timeframe="1d",
        as_of=_as_of(),
        lookback_days=30,
    )

    assert brief.analyst == "sentiment"
    assert brief.stance == "bullish"

    user_prompt = llm.calls[0]["user"]
    # Verify web search results appear in prompt
    assert "web_search_results" in user_prompt or "web_results" in user_prompt.lower()
    assert "cn_stock" in user_prompt or "market_type" in user_prompt


# ────────────────────────────────────────────────────────────────────
# Valuation（D-10，相对估值，借鉴 financial-services comps）
# ────────────────────────────────────────────────────────────────────


@respx.mock
async def test_valuation_anchors_on_real_fundamentals(data_client: DataClient) -> None:
    """有 fundamentals 快照 → valuation_inputs 进 prompt，brief.analyst == 'valuation'。"""
    respx.get("http://data-mock.test/fundamentals").mock(
        return_value=Response(
            200,
            json={
                "available": True,
                "indicators": {
                    "market_cap": 2.3e12,
                    "pe_ratio": 28.5,
                    "pb_ratio": 12.1,
                    "roe": 0.31,
                    "net_margin": 0.25,
                },
            },
        )
    )
    respx.get("http://data-mock.test/web/search").mock(
        return_value=Response(200, json={"results": []})
    )

    llm = FakeLLMClient(
        {
            "you are a relative valuation analyst": {
                "stance": "bearish",
                "confidence": 0.5,
                "summary": "PE/PB rich vs the ROE profile.",
                "key_points": ["PE 28.5 elevated", "PB 12 demanding"],
                "factors": [
                    {
                        "name": "pe_vs_quality",
                        "kind": "macro",
                        "value": "rich",
                        "strength": 0.5,
                        "horizon": "position",
                        "explanation": "PE high relative to growth/ROE",
                    }
                ],
            }
        }
    )
    analyst = ValuationAnalyst(llm=llm, data=data_client)
    brief = await analyst.run(
        venue="alpaca",
        symbol="AAPL",
        timeframe="1d",
        as_of=_as_of(),
        lookback_days=30,
    )

    assert brief.analyst == "valuation"
    assert brief.stance == "bearish"
    # valuation factor 以 macro kind 编码（schema 无专门 valuation kind）
    assert brief.factors and brief.factors[0].kind == "macro"

    user_prompt = llm.calls[0]["user"]
    assert "valuation_inputs" in user_prompt
    assert "PE ratio" in user_prompt
    # 关键纪律：prompt 显式要求"只做相对估值、不做 DCF"
    assert "relative valuation only" in user_prompt.lower()
    # venue 路由（D-12）：alpaca 研究 → fundamentals 走 yfinance，不再 422
    fund_route = respx.get("http://data-mock.test/fundamentals")
    assert fund_route.calls.last.request.url.params["venue"] == "yfinance"


@respx.mock
async def test_valuation_degrades_when_no_fundamentals(data_client: DataClient) -> None:
    """没有 fundamentals → prompt 提示 qualitative-only + cap 0.55，仍返 brief 不抛。"""
    respx.get("http://data-mock.test/fundamentals").mock(
        return_value=Response(200, json={"available": False, "reason": "no data"})
    )
    respx.get("http://data-mock.test/web/search").mock(
        return_value=Response(200, json={"results": []})
    )

    llm = FakeLLMClient(
        {
            "you are a relative valuation analyst": {
                "stance": "neutral",
                "confidence": 0.55,
                "summary": "No live multiples; qualitative only.",
                "key_points": ["no peer data"],
            }
        }
    )
    analyst = ValuationAnalyst(llm=llm, data=data_client)
    brief = await analyst.run(
        venue="binance",
        symbol="BTC/USDT",
        timeframe="1h",
        as_of=_as_of(),
        lookback_days=30,
    )

    assert brief.analyst == "valuation"
    user_prompt = llm.calls[0]["user"]
    assert "not available" in user_prompt
    assert "qualitative" in user_prompt.lower()
