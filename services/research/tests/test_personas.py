"""投资大师人格 analyst 单测（ADR-0037 §A）—— FakeLLM + respx mock 数据源。"""
from __future__ import annotations

from datetime import UTC, datetime

import pytest
import respx
from httpx import Response

from inalpha_research.analysts.personas import PERSONA_ANALYSTS
from inalpha_research.data_client import DataClient
from inalpha_research.llm.client import FakeLLMClient
from inalpha_research.schemas import AnalystBrief

# persona key → (FakeLLM 锚定词, 期望 type_id) —— 锚定词须是各 _SYSTEM 开头大师全名
_PERSONA_CASES = {
    "buffett": ("you are warren buffett", "persona_buffett"),
    "lynch": ("you are peter lynch", "persona_lynch"),
    "wood": ("you are cathie wood", "persona_wood"),
    "burry": ("you are michael burry", "persona_burry"),
    "druckenmiller": ("you are stanley druckenmiller", "persona_druckenmiller"),
    "marks": ("you are howard marks", "persona_marks"),
}


def _as_of() -> datetime:
    return datetime(2026, 5, 21, 12, 0, tzinfo=UTC)


@pytest.fixture
async def data_client() -> DataClient:
    return DataClient(base_url="http://data-mock.test", jwt_token="t")


def test_registry_covers_six_low_correlation_personas() -> None:
    """注册表恰好暴露计划里的 6 个人格，且 type_id 命名规范一致。"""
    assert set(PERSONA_ANALYSTS) == set(_PERSONA_CASES)
    for key, cls in PERSONA_ANALYSTS.items():
        assert cls.type_id == f"persona_{key}"


@respx.mock
@pytest.mark.parametrize("key", list(_PERSONA_CASES))
async def test_persona_returns_valid_brief(key: str, data_client: DataClient) -> None:
    """每个 persona：命中预设 → 合法 AnalystBrief，analyst 字段是 persona_<key>。"""
    anchor, expected_type = _PERSONA_CASES[key]
    respx.get("http://data-mock.test/fundamentals").mock(
        return_value=Response(200, json={"available": False, "reason": "crypto"})
    )
    respx.get("http://data-mock.test/web/search").mock(
        return_value=Response(200, json={"results": []})
    )

    llm = FakeLLMClient(
        {
            anchor: {
                "stance": "neutral",
                "confidence": 0.5,
                "summary": f"{key} verdict in voice.",
                "key_points": ["p1", "p2"],
            }
        }
    )
    analyst = PERSONA_ANALYSTS[key](llm=llm, data=data_client)
    brief = await analyst.run(
        venue="binance",
        symbol="BTC/USDT",
        timeframe="1h",
        as_of=_as_of(),
        lookback_days=30,
    )

    assert isinstance(brief, AnalystBrief)
    assert brief.analyst == expected_type
    assert brief.stance == "neutral"


@respx.mock
async def test_persona_anchors_on_real_fundamentals(data_client: DataClient) -> None:
    """有 fundamentals 快照（美股）→ 指标进 user prompt，多市场 vocabulary 就位。"""
    respx.get("http://data-mock.test/fundamentals").mock(
        return_value=Response(
            200,
            json={
                "available": True,
                "indicators": {"market_cap": 2.3e12, "pe_ratio": 28.5, "roe": 0.31},
            },
        )
    )
    respx.get("http://data-mock.test/web/search").mock(
        return_value=Response(200, json={"results": []})
    )

    llm = FakeLLMClient(
        {
            "you are warren buffett": {
                "stance": "bearish",
                "confidence": 0.6,
                "summary": "Wide moat but price offers no margin of safety.",
                "key_points": ["wide moat", "expensive"],
                "factors": [
                    {
                        "name": "moat_width",
                        "kind": "macro",
                        "value": "wide",
                        "strength": 0.7,
                        "horizon": "position",
                        "explanation": "durable brand + scale",
                    }
                ],
            }
        }
    )
    analyst = PERSONA_ANALYSTS["buffett"](llm=llm, data=data_client)
    brief = await analyst.run(
        venue="alpaca",
        symbol="AAPL",
        timeframe="1d",
        as_of=_as_of(),
        lookback_days=30,
    )

    assert brief.analyst == "persona_buffett"
    assert brief.stance == "bearish"
    # persona factor 以 macro kind 编码（schema 无 persona kind）
    assert brief.factors and brief.factors[0].kind == "macro"

    user_prompt = llm.calls[0]["user"]
    assert "fundamentals" in user_prompt
    assert "市盈率 PE" in user_prompt
    assert "market_type: us_stock" in user_prompt


@respx.mock
async def test_persona_degrades_without_fundamentals(data_client: DataClient) -> None:
    """无 fundamentals（crypto）→ prompt 提示 qualitative + cap 0.55，仍返 brief 不抛。"""
    respx.get("http://data-mock.test/fundamentals").mock(
        return_value=Response(200, json={"available": False, "reason": "no data"})
    )
    respx.get("http://data-mock.test/web/search").mock(
        return_value=Response(200, json={"results": []})
    )

    llm = FakeLLMClient(
        {
            "you are michael burry": {
                "stance": "neutral",
                "confidence": 0.55,
                "summary": "Outside my circle without hard value; staying flat.",
                "key_points": ["no hard value data"],
            }
        }
    )
    analyst = PERSONA_ANALYSTS["burry"](llm=llm, data=data_client)
    brief = await analyst.run(
        venue="binance",
        symbol="BTC/USDT",
        timeframe="1h",
        as_of=_as_of(),
        lookback_days=30,
    )

    assert brief.analyst == "persona_burry"
    user_prompt = llm.calls[0]["user"]
    assert "not available" in user_prompt
    assert "0.55" in user_prompt
