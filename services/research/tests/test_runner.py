"""runner.run_deep_dive 集成单测 —— 把 analysts + manager 接起来跑。"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import respx
from httpx import Response
from pydantic import ValidationError

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
async def test_deep_dive_prefetch_dedups_shared_bars(fake_llm: FakeLLMClient) -> None:
    """D-13 · P0 回归：预取命中后 technical + risk 消费 shared.bars，不再自拉同一批。

    锁住上一轮 CI 才带出的静默降级——若 _prefetch_shared 每次抛异常被吞掉、shared
    永远 None，technical 与 risk 会各自再打一次 /bars。命中时它们读 shared：
    对 req.venue/symbol 的 K 线只在预取时打 1 次。
    （macro 拉的是 venue=fred 的宏观序列，是独立必需请求，不能共享——所以 /bars mock
    的总 call_count = 预取 1 + macro 的 fred 1 = 2，而非退化态的 4。）
    """
    bars = [
        make_bar_row((_as_of() - timedelta(hours=60 - i)).isoformat(), close=100 + i * 0.1)
        for i in range(60)
    ]
    # 按 symbol 区分两类 bars 请求：主标的（BTC/USDT）vs macro 拉的 FRED 序列。
    # 预取命中时 technical + risk 读 shared → 主标的的 /bars 只在预取时打 1 次。
    main_bars = respx.get(
        "http://data-mock.test/bars", params__contains={"symbol": "BTC/USDT"}
    ).mock(return_value=Response(200, json=bars))
    # macro 的 FRED 序列 + 其它兜底：不精确计数，返空/占位即可。
    respx.get("http://data-mock.test/bars").mock(return_value=Response(200, json=bars))
    respx.get("https://api.alternative.me/fng/").mock(
        return_value=Response(200, json={"data": [{"value": "40", "value_classification": "Fear", "timestamp": "1716163200"}]})
    )

    req = DeepDiveRequest(
        venue="binance",
        symbol="BTC/USDT",
        timeframe="1h",
        as_of=_as_of(),
        lookback_days=7,
    )

    async with DataClient("http://data-mock.test", "t") as data:
        await run_deep_dive(req, llm=fake_llm, data=data)

    # 主标的 K 线：预取打 1 次，technical + risk 都读 shared 不再自拉 → 恰好 1。
    # 退化态（shared=None）会是 technical + risk 各自拉 = 2+。
    assert main_bars.call_count == 1


@respx.mock
async def test_deep_dive_with_personas_appends_master_briefs(
    fake_llm: FakeLLMClient,
) -> None:
    """ADR-0037 §A：req.personas 指定时，核心 6 analyst 之外追加对应大师 brief。

    - 默认路径（无 personas）仍是 6 brief —— 见 ``test_deep_dive_runs_full_chain``。
    - 指定 ``["buffett", "wood"]`` → 8 brief，含 ``persona_buffett`` / ``persona_wood``。
    - 无效 key 现在由 ``DeepDiveRequest`` field_validator 在边界拒绝（422），见
      ``test_deep_dive_request_rejects_unknown_persona``。
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
        personas=["buffett", "wood"],
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


def test_deep_dive_request_rejects_unknown_persona() -> None:
    """未知 persona key 在 DeepDiveRequest 边界即被拒（422），而非静默丢弃。

    保护直接调用 Python 服务的场景（集成测试 / 未来新调用方）——orchestrator 侧
    TS z.enum 已挡，但 Python list[str] 之前无校验。
    """
    with pytest.raises(ValidationError) as ei:
        DeepDiveRequest(
            venue="binance",
            symbol="BTC/USDT",
            timeframe="1h",
            as_of=_as_of(),
            personas=["buffett", "not_a_real_persona"],
        )
    assert "unknown persona key" in str(ei.value)
    # 合法 key 不受影响
    ok = DeepDiveRequest(
        venue="binance", symbol="BTC/USDT", timeframe="1h", as_of=_as_of(),
        personas=["buffett", "wood"],
    )
    assert ok.personas == ["buffett", "wood"]


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


def _mock_bars_and_fng() -> None:
    """辩论类用例共用的数据源 mock：60 根缓涨 bar + FNG 极恐（喂出 fixture 预设 briefs）。"""
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


@respx.mock
async def test_deep_dive_with_debate_includes_bull_bear_log(
    fake_llm: FakeLLMClient,
    monkeypatch: Any,
) -> None:
    """trigger=always + 两方制（旧 D-9 行为保留档）：6 analyst + Bull + Bear +
    manager = 9 次 LLM 调用，且 ``plan.debate_log`` 落 2 条 turn。

    fixture briefs 全员 bullish/neutral（aligned），默认 contested 触发会跳过辩论——
    本用例显式 always 验证旧行为开关仍可用；新默认路径见下两个用例。"""
    from inalpha_research.config import get_research_settings

    monkeypatch.setenv("RESEARCH_MAX_DEBATE_ROUNDS", "1")
    monkeypatch.setenv("RESEARCH_DEBATE_TRIGGER", "always")
    monkeypatch.setenv("RESEARCH_DEBATE_RISK_ENABLED", "false")
    get_research_settings.cache_clear()
    try:
        _mock_bars_and_fng()

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
        # 决策链路字段（#6）：always 模式如实落盘
        assert plan.debate_trigger is not None and plan.debate_trigger.startswith("always:")
        assert plan.debate_stop_reason == "completed 1 round(s)"
    finally:
        # 还原 env 避免污染后续测试
        get_research_settings.cache_clear()


@respx.mock
async def test_deep_dive_contested_triggers_three_way_debate(
    fake_llm: FakeLLMClient,
    monkeypatch: Any,
) -> None:
    """research-hub #6 默认路径：briefs 出现多空对立（persona burry 提供 bearish）
    → 触发三方辩论（Bull/Bear/Risk），决策链路字段全落盘。"""
    from inalpha_research.config import get_research_settings

    monkeypatch.setenv("RESEARCH_MAX_DEBATE_ROUNDS", "1")
    get_research_settings.cache_clear()
    try:
        _mock_bars_and_fng()

        req = DeepDiveRequest(
            venue="binance",
            symbol="BTC/USDT",
            timeframe="1h",
            as_of=_as_of(),
            lookback_days=7,
            personas=["burry"],  # bearish 0.55 ↔ technical bullish 0.7 → contested
        )

        async with DataClient("http://data-mock.test", "t") as data:
            plan = await run_deep_dive(req, llm=fake_llm, data=data)

        # 11 = 6 analyst + 1 persona + Bull + Bear + Risk + manager
        assert len(fake_llm.calls) == 11
        assert [(t.role, t.round) for t in plan.debate_log] == [
            ("bull", 1),
            ("bear", 1),
            ("risk", 1),
        ]
        # 决策链路（#6）：为什么辩了 / 为什么停 / manager 怎么权衡
        assert plan.debate_trigger is not None and plan.debate_trigger.startswith("contested:")
        assert plan.debate_stop_reason == "completed 1 round(s)"
        assert plan.synthesis_reasoning is not None
        assert "technical analyst" in plan.synthesis_reasoning
        # manager 能读到 Risk 的发言
        manager_call = next(
            c for c in fake_llm.calls if "research manager" in c["system"].lower()
        )
        assert "Round 1 RISK" in manager_call["user"]
    finally:
        get_research_settings.cache_clear()


@respx.mock
async def test_deep_dive_aligned_briefs_skip_debate(
    fake_llm: FakeLLMClient,
    monkeypatch: Any,
) -> None:
    """research-hub #6：fixture briefs 全员 bullish/neutral（aligned）→ 默认
    contested 触发下跳过辩论省 token，跳过原因落 ``plan.debate_trigger``。"""
    from inalpha_research.config import get_research_settings

    monkeypatch.setenv("RESEARCH_MAX_DEBATE_ROUNDS", "1")
    get_research_settings.cache_clear()
    try:
        _mock_bars_and_fng()

        req = DeepDiveRequest(
            venue="binance",
            symbol="BTC/USDT",
            timeframe="1h",
            as_of=_as_of(),
            lookback_days=7,
        )

        async with DataClient("http://data-mock.test", "t") as data:
            plan = await run_deep_dive(req, llm=fake_llm, data=data)

        # 7 = 6 analyst + manager（Bull/Bear/Risk 都没起跑）
        assert len(fake_llm.calls) == 7
        assert plan.debate_log == []
        assert plan.debate_trigger is not None
        assert plan.debate_trigger.startswith("skipped: no confident opposing stances")
        assert plan.debate_stop_reason is None
    finally:
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
