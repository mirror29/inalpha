"""5 类 analyst 的 multi-market 感知测试（D-9 升级）。

覆盖：

- 每个 analyst 的 user prompt 都含 ``market_type`` 字段
- sentiment 在非 crypto 不调 ``alternative.me/fng/``（走 LLM-only）
- risk system prompt 含 5 类 vol band 表
- macro system prompt 含传导表
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import respx
from httpx import Response

from inalpha_research.analysts.macro import MacroAnalyst
from inalpha_research.analysts.risk import RiskAnalyst
from inalpha_research.analysts.sentiment import SentimentAnalyst
from inalpha_research.analysts.technical import TechnicalAnalyst
from inalpha_research.data_client import DataClient
from inalpha_research.llm.client import FakeLLMClient

from .conftest import make_bar_row


def _as_of() -> datetime:
    return datetime(2026, 5, 21, 12, 0, tzinfo=UTC)


def _stock_brief() -> dict[str, Any]:
    return {
        "stance": "neutral",
        "confidence": 0.5,
        "summary": "stock multi-market test",
        "key_points": ["kp"],
    }


# ────────────────────────────────────────────────────────────────────
# 1) 4 个 analyst 都把 market_type 塞 user prompt
# ────────────────────────────────────────────────────────────────────


@respx.mock
async def test_technical_user_prompt_has_market_type() -> None:
    bars = [make_bar_row((_as_of() - timedelta(hours=20 - i)).isoformat(), close=100 + i)
            for i in range(20)]
    respx.get("http://data-mock.test/bars").mock(return_value=Response(200, json=bars))

    llm = FakeLLMClient({"you are a technical analyst": _stock_brief()})
    async with DataClient("http://data-mock.test", "t") as data:
        analyst = TechnicalAnalyst(llm=llm, data=data)
        await analyst.run(
            venue="alpaca",
            symbol="AAPL",
            timeframe="1h",
            as_of=_as_of(),
            lookback_days=2,
        )

    user_prompt = llm.calls[0]["user"]
    assert "market_type: us_stock" in user_prompt
    assert "AAPL @ alpaca" in user_prompt


@respx.mock
async def test_risk_user_prompt_has_market_type() -> None:
    bars = [make_bar_row((_as_of() - timedelta(hours=120 - i)).isoformat(), close=100 + i * 0.1)
            for i in range(120)]
    respx.get("http://data-mock.test/bars").mock(return_value=Response(200, json=bars))

    llm = FakeLLMClient({"you are a risk analyst": _stock_brief()})
    async with DataClient("http://data-mock.test", "t") as data:
        analyst = RiskAnalyst(llm=llm, data=data)
        await analyst.run(
            venue="akshare",
            symbol="sh.600519",
            timeframe="1d",
            as_of=_as_of(),
            lookback_days=180,
        )

    user_prompt = llm.calls[0]["user"]
    assert "market_type: cn_stock" in user_prompt


async def test_macro_user_prompt_has_market_type() -> None:
    """macro analyst 不打 K 线接口，直接调即可。"""
    llm = FakeLLMClient({"you are a macro analyst": _stock_brief()})
    # data_client 不会被 macro 用到，传一个 placeholder
    async with DataClient("http://data-mock.test", "t") as data:
        analyst = MacroAnalyst(llm=llm, data=data)
        await analyst.run(
            venue="yfinance",
            symbol="^N225",
            timeframe="1d",
            as_of=_as_of(),
            lookback_days=14,
        )

    user_prompt = llm.calls[0]["user"]
    # ^N225 是 yfinance 指数 → global_stock
    assert "market_type: global_stock" in user_prompt


# ────────────────────────────────────────────────────────────────────
# 2) sentiment 在非 crypto 跳过 FNG，走 LLM-only
# ────────────────────────────────────────────────────────────────────


async def test_sentiment_skips_fng_for_non_crypto() -> None:
    """venue=alpaca 时 sentiment 不应调 alternative.me。"""
    llm = FakeLLMClient(
        {
            "you are a sentiment analyst": {
                "stance": "neutral",
                "confidence": 0.4,
                "summary": "LLM-only stock sentiment",
                "key_points": ["regime unclear"],
            }
        }
    )

    # 故意不 respx.mock alternative.me —— 如果 analyst 调它会抛
    async with DataClient("http://data-mock.test", "t") as data:
        analyst = SentimentAnalyst(llm=llm, data=data)
        brief = await analyst.run(
            venue="alpaca",
            symbol="AAPL",
            timeframe="1h",
            as_of=_as_of(),
            lookback_days=7,
        )

    assert brief.stance == "neutral"
    user_prompt = llm.calls[0]["user"]
    assert "market_type: us_stock" in user_prompt
    assert "non-crypto" in user_prompt
    # crypto_fng 区段应是 LLM-only fallback 提示
    # D-9 L3：非 crypto sentiment 现在拉 yfinance news（拉不到时清晰标注）
    assert "no Fear & Greed" in user_prompt
    assert "live_news" in user_prompt


@respx.mock
async def test_sentiment_still_uses_fng_for_crypto() -> None:
    """venue=binance 时 sentiment 仍走 FNG 路径（向后兼容）。"""
    respx.get("https://api.alternative.me/fng/").mock(
        return_value=Response(
            200,
            json={
                "data": [
                    {"value": "30", "value_classification": "Fear", "timestamp": "1716163200"},
                    *[
                        {"value": "35", "value_classification": "Fear", "timestamp": str(1716163200 - i * 86400)}
                        for i in range(1, 30)
                    ],
                ]
            },
        )
    )
    llm = FakeLLMClient(
        {
            "you are a sentiment analyst": {
                "stance": "neutral",
                "confidence": 0.5,
                "summary": "crypto fng neutral",
            }
        }
    )

    async with DataClient("http://data-mock.test", "t") as data:
        analyst = SentimentAnalyst(llm=llm, data=data)
        await analyst.run(
            venue="binance",
            symbol="BTC/USDT",
            timeframe="1h",
            as_of=_as_of(),
            lookback_days=7,
        )

    user_prompt = llm.calls[0]["user"]
    assert "market_type: crypto" in user_prompt
    assert "latest_value: 30" in user_prompt


# ────────────────────────────────────────────────────────────────────
# 3) risk / macro system prompt 含 multi-market 表
# ────────────────────────────────────────────────────────────────────


def test_risk_system_prompt_contains_all_market_bands() -> None:
    sys = RiskAnalyst.system_prompt(RiskAnalyst.__new__(RiskAnalyst))
    for mt in ("crypto", "us_stock", "cn_stock", "hk_stock", "global_stock"):
        assert mt in sys
    # 阈值表关键字段
    assert "low vol" in sys
    assert "high vol" in sys


def test_macro_system_prompt_contains_transmission_table() -> None:
    sys = MacroAnalyst.system_prompt(MacroAnalyst.__new__(MacroAnalyst))
    for mt in ("crypto", "us_stock", "cn_stock", "hk_stock", "global_stock"):
        assert mt in sys
    # 传导表关键字
    assert "Transmission" in sys or "transmission" in sys
    assert "USD-peg" in sys  # 港股专属
