"""runner.run_deep_dive 集成单测 —— 把 analysts + manager 接起来跑。"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import respx
from httpx import Response

from inalpha_research.data_client import DataClient
from inalpha_research.llm.client import FakeLLMClient
from inalpha_research.runner import run_deep_dive
from inalpha_research.schemas import DeepDiveRequest

from .conftest import make_bar_row


def _as_of() -> datetime:
    return datetime(2026, 5, 21, 12, 0, tzinfo=UTC)


@respx.mock
async def test_deep_dive_runs_full_chain(fake_llm: FakeLLMClient) -> None:
    """6 个 analyst 并行 + manager 综合 → ResearchPlan。

    sentiment 这条因为不 mock api.alternative.me 会失败兜底，但仍计入 briefs。
    """
    bars = [
        make_bar_row((_as_of() - timedelta(hours=60 - i)).isoformat(), close=100 + i * 0.1)
        for i in range(60)
    ]
    respx.get("http://data-mock.test/bars").mock(return_value=Response(200, json=bars))
    # 给 sentiment 也提供成功路径 —— 让全 5 analyst 都成功
    respx.get("https://api.alternative.me/fng/").mock(
        return_value=Response(
            200,
            json={
                "data": [
                    {"value": "22", "value_classification": "Extreme Fear", "timestamp": "1716163200"},
                    *[
                        {"value": str(30 + i % 10), "value_classification": "Fear", "timestamp": str(1716163200 - i * 86400)}
                        for i in range(1, 30)
                    ],
                ]
            },
        )
    )

    req = DeepDiveRequest(
        venue="binance",
        symbol="BTC/USDT",
        timeframe="1h",
        as_of=_as_of(),
        lookback_days=7,
        user_question="should I buy BTC?",
    )

    async with DataClient("http://data-mock.test", "t") as data:
        plan = await run_deep_dive(req, llm=fake_llm, data=data)

    # plan 字段就位
    assert plan.symbol == "BTC/USDT"
    assert plan.rating in ("overweight", "neutral", "underweight")
    assert plan.confidence == 0.65  # conftest fixture
    # 6 个 brief 都该在（D-10 加 valuation）
    analysts_seen = {b.analyst for b in plan.briefs}
    assert analysts_seen == {
        "technical",
        "fundamental",
        "sentiment",
        "risk",
        "macro",
        "valuation",
    }
    # LLM 调 ≥ 7 次（6 analyst + 1 manager；可能含 Bull/Bear 辩论的 2N 轮）
    assert len(fake_llm.calls) >= 7


@respx.mock
async def test_deep_dive_with_personas_appends_master_briefs(
    fake_llm: FakeLLMClient,
) -> None:
    """ADR-0037 §A：req.personas 指定时，核心 6 analyst 之外追加对应大师 brief。

    - 默认路径（无 personas）仍是 6 brief —— 见 ``test_deep_dive_runs_full_chain``。
    - 指定 ``["buffett", "wood"]`` → 8 brief，含 ``persona_buffett`` / ``persona_wood``。
    - 无效 key 被静默忽略，不增加 brief、不抛。
    """
    bars = [
        make_bar_row((_as_of() - timedelta(hours=60 - i)).isoformat(), close=100 + i * 0.1)
        for i in range(60)
    ]
    respx.get("http://data-mock.test/bars").mock(return_value=Response(200, json=bars))
    respx.get("https://api.alternative.me/fng/").mock(
        return_value=Response(
            200,
            json={
                "data": [
                    {"value": "22", "value_classification": "Extreme Fear", "timestamp": "1716163200"},
                    *[
                        {"value": str(30 + i % 10), "value_classification": "Fear", "timestamp": str(1716163200 - i * 86400)}
                        for i in range(1, 30)
                    ],
                ]
            },
        )
    )
    # persona 的 fundamentals / web 搜索调用（buffett / wood 都会拉）
    respx.get("http://data-mock.test/fundamentals").mock(
        return_value=Response(200, json={"available": False, "reason": "crypto"})
    )
    respx.get("http://data-mock.test/web/search").mock(
        return_value=Response(200, json={"results": []})
    )

    req = DeepDiveRequest(
        venue="binance",
        symbol="BTC/USDT",
        timeframe="1h",
        as_of=_as_of(),
        lookback_days=7,
        personas=["buffett", "wood", "not_a_real_persona"],  # 无效 key 应被忽略
    )

    async with DataClient("http://data-mock.test", "t") as data:
        plan = await run_deep_dive(req, llm=fake_llm, data=data)

    analysts_seen = {b.analyst for b in plan.briefs}
    # 核心 6 + 2 个有效 persona = 8；无效 key 不计入
    assert analysts_seen == {
        "technical",
        "fundamental",
        "sentiment",
        "risk",
        "macro",
        "valuation",
        "persona_buffett",
        "persona_wood",
    }
    assert len(plan.briefs) == 8


@respx.mock
async def test_deep_dive_dedups_duplicate_personas(fake_llm: FakeLLMClient) -> None:
    """重复 persona key 去重保序：``["buffett","buffett","wood"]`` 只各跑一次。

    否则同一大师被追加多个 analyst → 多条相同 brief，manager 综合时该视角被人为
    加权 + 多耗 LLM 调用。
    """
    bars = [
        make_bar_row((_as_of() - timedelta(hours=60 - i)).isoformat(), close=100 + i * 0.1)
        for i in range(60)
    ]
    respx.get("http://data-mock.test/bars").mock(return_value=Response(200, json=bars))
    respx.get("https://api.alternative.me/fng/").mock(
        return_value=Response(200, json={"data": [{"value": "22", "value_classification": "Extreme Fear", "timestamp": "1716163200"}]})
    )
    respx.get("http://data-mock.test/fundamentals").mock(
        return_value=Response(200, json={"available": False, "reason": "crypto"})
    )
    respx.get("http://data-mock.test/web/search").mock(
        return_value=Response(200, json={"results": []})
    )

    req = DeepDiveRequest(
        venue="binance",
        symbol="BTC/USDT",
        timeframe="1h",
        as_of=_as_of(),
        lookback_days=7,
        personas=["buffett", "buffett", "wood"],  # 重复 buffett
    )

    async with DataClient("http://data-mock.test", "t") as data:
        plan = await run_deep_dive(req, llm=fake_llm, data=data)

    analyst_list = [b.analyst for b in plan.briefs]
    # buffett 只出现一次（去重生效），不是两次
    assert analyst_list.count("persona_buffett") == 1
    assert analyst_list.count("persona_wood") == 1
    # 核心 6 + 去重后 2 = 8
    assert len(plan.briefs) == 8


@respx.mock
async def test_deep_dive_continues_when_some_analysts_fail() -> None:
    """data-service 500 + 无 FNG mock 让 technical/risk/sentiment 失败，
    fundamental + macro 仍能跑（不依赖外部数据），manager 综合不抛。"""
    respx.get("http://data-mock.test/bars").mock(
        return_value=Response(500, json={"code": "DB_DOWN", "message": "down"})
    )
    # FNG 故意 500 让 sentiment 失败
    respx.get("https://api.alternative.me/fng/").mock(
        return_value=Response(500, json={"error": "down"})
    )

    llm = FakeLLMClient(
        {
            # macro / fundamental 共用一份预设（两个 system prompt 都含 'fundamental' 子串）
            "you are a fundamental / macro analyst": {
                "stance": "neutral",
                "confidence": 0.4,
                "summary": "macro mixed",
            },
            "you are a macro analyst": {
                "stance": "neutral",
                "confidence": 0.4,
                "summary": "macro calendar quiet",
            },
            "you are a research manager": {
                "rating": "neutral",
                "confidence": 0.4,
                "thesis": "limited visibility — technical / risk / sentiment all down",
                "risks": ["data-service down → reduced visibility"],
                "suggested_action": "wait",
                "horizon": "swing",
            },
        }
    )

    req = DeepDiveRequest(
        venue="binance",
        symbol="BTC/USDT",
        timeframe="1h",
        as_of=_as_of(),
        lookback_days=7,
    )

    async with DataClient("http://data-mock.test", "t") as data:
        plan = await run_deep_dive(req, llm=llm, data=data)

    # 仍然返 plan，不抛
    assert plan.rating == "neutral"
    # 失败的 analyst 都留 placeholder brief
    for failed_type in ("technical", "risk", "sentiment"):
        b = next(b for b in plan.briefs if b.analyst == failed_type)
        assert b.confidence == 0.0
        assert b.summary.startswith("(analyst failed)")


@respx.mock
async def test_deep_dive_with_debate_includes_bull_bear_log(
    fake_llm: FakeLLMClient,
    monkeypatch: Any,
) -> None:
    """开启 1 轮辩论：6 analyst + Bull + Bear + manager = 9 次 LLM 调用，
    且 ``plan.debate_log`` 落 2 条 turn。"""
    from inalpha_research.config import get_research_settings

    monkeypatch.setenv("RESEARCH_MAX_DEBATE_ROUNDS", "1")
    get_research_settings.cache_clear()
    try:
        bars = [
            make_bar_row((_as_of() - timedelta(hours=60 - i)).isoformat(), close=100 + i * 0.1)
            for i in range(60)
        ]
        respx.get("http://data-mock.test/bars").mock(return_value=Response(200, json=bars))
        respx.get("https://api.alternative.me/fng/").mock(
            return_value=Response(
                200,
                json={
                    "data": [
                        {"value": "22", "value_classification": "Extreme Fear", "timestamp": "1716163200"},
                        *[
                            {"value": str(30 + i % 10), "value_classification": "Fear", "timestamp": str(1716163200 - i * 86400)}
                            for i in range(1, 30)
                        ],
                    ]
                },
            )
        )

        req = DeepDiveRequest(
            venue="binance",
            symbol="BTC/USDT",
            timeframe="1h",
            as_of=_as_of(),
            lookback_days=7,
        )

        async with DataClient("http://data-mock.test", "t") as data:
            plan = await run_deep_dive(req, llm=fake_llm, data=data)

        # 9 = 6 analyst + Bull + Bear + manager
        assert len(fake_llm.calls) == 9
        # 辩论日志落进 plan
        assert len(plan.debate_log) == 2
        assert plan.debate_log[0].role == "bull"
        assert plan.debate_log[0].round == 1
        assert plan.debate_log[1].role == "bear"
        assert plan.debate_log[1].round == 1
        # manager user prompt 应含 "debate_log"
        manager_call = next(
            c for c in fake_llm.calls if "research manager" in c["system"].lower()
        )
        assert "debate_log" in manager_call["user"]
        assert "Round 1 BULL" in manager_call["user"]
        assert "Round 1 BEAR" in manager_call["user"]
    finally:
        # 还原 env 避免污染后续测试
        get_research_settings.cache_clear()


@respx.mock
async def test_deep_dive_passes_user_question_to_manager(fake_llm: FakeLLMClient) -> None:
    bars = [make_bar_row(_as_of().isoformat()) for _ in range(15)]
    respx.get("http://data-mock.test/bars").mock(return_value=Response(200, json=bars))

    req = DeepDiveRequest(
        venue="binance",
        symbol="BTC/USDT",
        timeframe="1h",
        as_of=_as_of(),
        lookback_days=1,
        user_question="my-custom-question",
    )

    async with DataClient("http://data-mock.test", "t") as data:
        await run_deep_dive(req, llm=fake_llm, data=data)

    # manager 的 user prompt 应含 user_question 原文
    manager_calls: list[dict[str, Any]] = [
        c for c in fake_llm.calls if "research manager" in c["system"].lower()
    ]
    assert len(manager_calls) == 1
    assert "my-custom-question" in manager_calls[0]["user"]
