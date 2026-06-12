"""辩论协调器单测 —— 不打外部网络，用 FakeLLMClient 跑 Bull/Bear(/Risk) 轮换。"""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from inalpha_research.debate import assess_disagreement, run_debate
from inalpha_research.llm.client import FakeLLMClient
from inalpha_research.researchers import BearResearcher, BullResearcher, RiskResearcher
from inalpha_research.schemas import AnalystBrief, DebateTurn


def _as_of() -> datetime:
    return datetime(2026, 5, 21, 12, 0, tzinfo=UTC)


def _brief(analyst: str, stance: str = "neutral", confidence: float = 0.5) -> AnalystBrief:
    return AnalystBrief(
        analyst=analyst,  # type: ignore[arg-type]
        stance=stance,  # type: ignore[arg-type]
        confidence=confidence,
        summary=f"{analyst} brief",
        key_points=[f"{analyst} kp1"],
    )


async def _debate_turns(**kwargs: Any) -> list[DebateTurn]:
    """旧断言风格适配层：只关心发言序列的用例直接取 ``outcome.turns``。"""
    return (await run_debate(**kwargs)).turns


def _bull_bear_llm() -> FakeLLMClient:
    return FakeLLMClient(
        {
            "you are a bull analyst": {"argument": "Bull says up"},
            "you are a bear analyst": {"argument": "Bear says down"},
        }
    )


async def test_run_debate_zero_rounds_returns_empty() -> None:
    """``max_rounds=0`` 时不调 LLM，直接返空 log（runner 旁路场景）。"""
    llm = _bull_bear_llm()
    bull = BullResearcher(llm=llm)
    bear = BearResearcher(llm=llm)

    log = await _debate_turns(
        bull=bull,
        bear=bear,
        venue="binance",
        symbol="BTC/USDT",
        timeframe="1h",
        as_of=_as_of(),
        briefs=[_brief("technical", "bullish")],
        max_rounds=0,
    )

    assert log == []
    assert llm.calls == []


async def test_run_debate_one_round_alternates_bull_then_bear() -> None:
    llm = _bull_bear_llm()
    bull = BullResearcher(llm=llm)
    bear = BearResearcher(llm=llm)

    log = await _debate_turns(
        bull=bull,
        bear=bear,
        venue="binance",
        symbol="BTC/USDT",
        timeframe="1h",
        as_of=_as_of(),
        briefs=[_brief("technical", "bullish"), _brief("risk", "neutral")],
        max_rounds=1,
    )

    assert len(log) == 2
    assert log[0].role == "bull"
    assert log[0].round == 1
    assert log[0].content == "Bull says up"
    assert log[1].role == "bear"
    assert log[1].round == 1
    assert log[1].content == "Bear says down"

    # Bull 第一轮 user prompt 不应含"opponent_last_turn"（第一发言）
    bull_call = next(c for c in llm.calls if "bull analyst" in c["system"].lower())
    assert "opponent_last_turn" not in bull_call["user"]

    # Bear 这次应该看到 Bull 上一轮（rebut）
    bear_call = next(c for c in llm.calls if "bear analyst" in c["system"].lower())
    assert "opponent_last_turn" in bear_call["user"]
    assert "Bull says up" in bear_call["user"]


async def test_run_debate_multi_round_grows_history() -> None:
    """2 轮 = 4 turns，且第 2 轮 Bull 应看到 Round1 Bear。"""
    llm = _bull_bear_llm()
    bull = BullResearcher(llm=llm)
    bear = BearResearcher(llm=llm)

    log = await _debate_turns(
        bull=bull,
        bear=bear,
        venue="binance",
        symbol="BTC/USDT",
        timeframe="1h",
        as_of=_as_of(),
        briefs=[_brief("technical")],
        max_rounds=2,
    )

    assert [(t.role, t.round) for t in log] == [
        ("bull", 1),
        ("bear", 1),
        ("bull", 2),
        ("bear", 2),
    ]

    # 4 次 LLM 调用
    assert len(llm.calls) == 4
    # 第 3 次（Round 2 Bull）应该看到 Round 1 Bear 的发言
    bull_round2 = llm.calls[2]
    assert "bull analyst" in bull_round2["system"].lower()
    assert "Bear says down" in bull_round2["user"]
    assert "Round 1 BEAR" in bull_round2["user"]


async def test_run_debate_round1_parallel_openings_when_multi_round() -> None:
    """#1：``max_rounds>=2`` 时第 1 轮 Bull/Bear 独立并行开场——

    round-1 Bear 不应看到 round-1 Bull（history 为空，无 opponent_last_turn）；
    而 round-2 Bear 是 rebuttal，应看到对手上一轮。落 log 顺序仍固定 Bull 在前。
    """
    llm = _bull_bear_llm()
    bull = BullResearcher(llm=llm)
    bear = BearResearcher(llm=llm)

    log = await _debate_turns(
        bull=bull,
        bear=bear,
        venue="binance",
        symbol="BTC/USDT",
        timeframe="1h",
        as_of=_as_of(),
        briefs=[_brief("technical")],
        max_rounds=2,
    )

    # 落 log 顺序固定（即使第 1 轮并行）
    assert [(t.role, t.round) for t in log] == [
        ("bull", 1),
        ("bear", 1),
        ("bull", 2),
        ("bear", 2),
    ]

    bear_calls = [c for c in llm.calls if "bear analyst" in c["system"].lower()]
    assert len(bear_calls) == 2
    # 有一次 Bear 没看到对手（round-1 并行开场）——这是 #1 的关键证据
    assert any("opponent_last_turn" not in c["user"] for c in bear_calls)
    # 也有一次 Bear 看到了对手（round-2 rebuttal）
    assert any("opponent_last_turn" in c["user"] for c in bear_calls)


async def test_run_debate_one_round_stays_serial_preserves_rebuttal() -> None:
    """#1 边界：``max_rounds==1`` 仍串行——保住那唯一一次 Bear 反驳 Bull 的价值。"""
    llm = _bull_bear_llm()
    bull = BullResearcher(llm=llm)
    bear = BearResearcher(llm=llm)

    await _debate_turns(
        bull=bull,
        bear=bear,
        venue="binance",
        symbol="BTC/USDT",
        timeframe="1h",
        as_of=_as_of(),
        briefs=[_brief("technical")],
        max_rounds=1,
    )

    bear_call = next(c for c in llm.calls if "bear analyst" in c["system"].lower())
    # 1 轮时 Bear 必须看到 Bull 的开场（串行 rebuttal 未被并行优化抹掉）
    assert "opponent_last_turn" in bear_call["user"]
    assert "Bull says up" in bear_call["user"]


async def test_run_debate_passes_max_tokens_to_llm() -> None:
    """#2：``max_tokens`` 透传到每次发言的 complete_json。"""
    llm = _bull_bear_llm()
    bull = BullResearcher(llm=llm)
    bear = BearResearcher(llm=llm)

    await _debate_turns(
        bull=bull,
        bear=bear,
        venue="binance",
        symbol="BTC/USDT",
        timeframe="1h",
        as_of=_as_of(),
        briefs=[_brief("technical")],
        max_rounds=1,
        max_tokens=600,
    )

    assert llm.calls, "应有 LLM 调用"
    assert all(c["max_tokens"] == 600 for c in llm.calls)


async def test_run_debate_timeout_returns_partial_log_without_raising() -> None:
    """#4：辩论超时返回已完成的部分 log，不抛错。

    on_call 故意 sleep 远超 timeout → bull 那次发言被取消、未落 log → 返空 log。
    """

    async def _slow(*, system: str, user: str) -> None:
        # 远大于 timeout（5s vs 0.05s），给慢 CI 留充足余量、避免 race flaky
        await asyncio.sleep(5.0)

    llm = FakeLLMClient(
        {
            "you are a bull analyst": {"argument": "Bull says up"},
            "you are a bear analyst": {"argument": "Bear says down"},
        },
        on_call=_slow,
    )
    bull = BullResearcher(llm=llm)
    bear = BearResearcher(llm=llm)

    log = await _debate_turns(
        bull=bull,
        bear=bear,
        venue="binance",
        symbol="BTC/USDT",
        timeframe="1h",
        as_of=_as_of(),
        briefs=[_brief("technical")],
        max_rounds=1,
        timeout_seconds=0.05,
    )

    # 不抛；in-flight 的 bull 发言被取消，没有任何 turn 落 log
    assert log == []


async def test_run_debate_swallows_researcher_failure() -> None:
    """LLM 抛错时辩论不中断，落"(researcher failed)"占位继续。"""
    llm = FakeLLMClient(
        {
            "you are a bull analyst": {"argument": "Bull ok"},
            # 没给 Bear 预设 → FakeLLMClient 抛 LLMError
        }
    )
    bull = BullResearcher(llm=llm)
    bear = BearResearcher(llm=llm)

    log = await _debate_turns(
        bull=bull,
        bear=bear,
        venue="binance",
        symbol="BTC/USDT",
        timeframe="1h",
        as_of=_as_of(),
        briefs=[_brief("technical")],
        max_rounds=1,
    )

    assert len(log) == 2
    assert log[0].content == "Bull ok"
    assert "(researcher failed:" in log[1].content
    assert "LLM_FAKE_NO_MATCH" in log[1].content or "LLMError" in log[1].content


async def test_researcher_speak_empty_argument_falls_back() -> None:
    """LLM 返空 argument 字段时不应让 ResearcherTurn.content 校验炸（min_length=1）。"""
    llm = FakeLLMClient({"you are a bull analyst": {"argument": ""}})
    bull = BullResearcher(llm=llm)

    text = await bull.speak(
        venue="binance",
        symbol="BTC/USDT",
        timeframe="1h",
        as_of=_as_of(),
        briefs=[_brief("technical")],
        history=[],
        round_no=1,
    )
    assert text == "(empty argument from LLM)"


async def test_researcher_speak_handles_missing_response_key() -> None:
    """LLM 返没有 argument key 的 JSON 时也兜底为占位文本。"""
    llm = FakeLLMClient({"you are a bear analyst": {"foo": "bar"}})
    bear = BearResearcher(llm=llm)

    text = await bear.speak(
        venue="binance",
        symbol="BTC/USDT",
        timeframe="1h",
        as_of=_as_of(),
        briefs=[_brief("technical")],
        history=[],
        round_no=1,
    )
    assert text == "(empty argument from LLM)"


def test_bull_system_prompt_adjusts_for_crypto_vs_stock() -> None:
    """Bull system prompt 在 5 类资产下文案不同（fundamental_note 切换）。"""
    bull = BullResearcher(llm=FakeLLMClient())
    crypto_prompt = bull.system_prompt(asset_type="crypto")
    us_prompt = bull.system_prompt(asset_type="us_stock")
    cn_prompt = bull.system_prompt(asset_type="cn_stock")
    hk_prompt = bull.system_prompt(asset_type="hk_stock")

    # crypto 谈 on-chain / halving，不谈 10-K / 年报
    assert "on-chain" in crypto_prompt or "halving" in crypto_prompt
    assert "10-K" not in crypto_prompt and "年报" not in crypto_prompt

    # 美股谈 10-K / EPS
    assert "10-K" in us_prompt or "EPS" in us_prompt
    assert "halving" not in us_prompt

    # A 股谈年报 / 北向
    assert "年报" in cn_prompt or "北向" in cn_prompt
    assert "halving" not in cn_prompt

    # 港股谈 Southbound / 互联互通
    assert "Southbound" in hk_prompt or "HKMA" in hk_prompt


# ────────────────────────────────────────────────────────────────────
# research-hub #6：三方制 / stop_reason / 软早停 / 争议判定
# ────────────────────────────────────────────────────────────────────


def _three_way_llm() -> FakeLLMClient:
    return FakeLLMClient(
        {
            "you are a bull analyst": {"argument": "Bull says up"},
            "you are a bear analyst": {"argument": "Bear says down"},
            "you are a risk officer": {"argument": "Risk challenges both"},
        }
    )


async def test_run_debate_risk_speaks_last_each_round() -> None:
    """三方制：Risk 每轮在 Bull/Bear 之后殿后发言，且能读到双方本轮论点。"""
    llm = _three_way_llm()

    outcome = await run_debate(
        bull=BullResearcher(llm=llm),
        bear=BearResearcher(llm=llm),
        risk=RiskResearcher(llm=llm),
        venue="binance",
        symbol="BTC/USDT",
        timeframe="1h",
        as_of=_as_of(),
        briefs=[_brief("technical", "bullish")],
        max_rounds=2,
    )

    assert [(t.role, t.round) for t in outcome.turns] == [
        ("bull", 1),
        ("bear", 1),
        ("risk", 1),
        ("bull", 2),
        ("bear", 2),
        ("risk", 2),
    ]
    assert outcome.stop_reason == "completed 2 round(s)"

    # Risk 第 1 轮（即使 Bull/Bear 并行开场）也必须读到双方开场论点
    risk_r1 = next(c for c in llm.calls if "risk officer" in c["system"].lower())
    assert "Bull says up" in risk_r1["user"]
    assert "Bear says down" in risk_r1["user"]
    # PR #81 CR：风险官中立——不应收到"驳斥对手"指令（那会偏向一方），
    # 而是对称的双向压测提示
    assert "rebut this directly!" not in risk_r1["user"]
    assert "challenge BOTH sides symmetrically" in risk_r1["user"]
    # PR #81 CR major：Risk 殿后导致 history 末尾是 Risk——Round 2 Bull 的
    # rebuttal 对象必须仍是 Bear 的论据，不能错指到 Risk 的中立压测内容
    bull_r2 = [c for c in llm.calls if "bull analyst" in c["system"].lower()][-1]
    assert "opponent_last_turn (rebut this directly!): Bear says down" in bull_r2["user"]
    assert "rebut this directly!): Risk challenges both" not in bull_r2["user"]
    bear_r2 = [c for c in llm.calls if "bear analyst" in c["system"].lower()][-1]
    assert "opponent_last_turn (rebut this directly!): Bull says up" in bear_r2["user"]


async def test_run_debate_without_risk_keeps_two_way() -> None:
    """``risk=None``（RESEARCH_DEBATE_RISK_ENABLED=false）退回 Bull/Bear 两方制。"""
    llm = _three_way_llm()

    outcome = await run_debate(
        bull=BullResearcher(llm=llm),
        bear=BearResearcher(llm=llm),
        venue="binance",
        symbol="BTC/USDT",
        timeframe="1h",
        as_of=_as_of(),
        briefs=[_brief("technical")],
        max_rounds=1,
    )

    assert [t.role for t in outcome.turns] == ["bull", "bear"]
    assert outcome.stop_reason == "completed 1 round(s)"


async def test_run_debate_converges_early_when_arguments_repeat() -> None:
    """软早停：双方从第 2 轮起复读同样论点 → 不跑满 max_rounds。"""
    llm = _three_way_llm()  # FakeLLM 每轮返回相同文本 = 重合度 1.0

    outcome = await run_debate(
        bull=BullResearcher(llm=llm),
        bear=BearResearcher(llm=llm),
        venue="binance",
        symbol="BTC/USDT",
        timeframe="1h",
        as_of=_as_of(),
        briefs=[_brief("technical")],
        max_rounds=4,
        convergence_threshold=0.6,
    )

    # 第 2 轮发现与第 1 轮逐字相同 → 停，省掉第 3/4 轮
    assert [(t.role, t.round) for t in outcome.turns] == [
        ("bull", 1),
        ("bear", 1),
        ("bull", 2),
        ("bear", 2),
    ]
    assert outcome.stop_reason.startswith("converged: round 2")


async def test_run_debate_convergence_disabled_at_threshold_one() -> None:
    """阈值 1.0 = 实际禁用：即使逐字复读也跑满轮数（保留旧行为）。"""
    llm = _three_way_llm()

    outcome = await run_debate(
        bull=BullResearcher(llm=llm),
        bear=BearResearcher(llm=llm),
        venue="binance",
        symbol="BTC/USDT",
        timeframe="1h",
        as_of=_as_of(),
        briefs=[_brief("technical")],
        max_rounds=3,
        convergence_threshold=1.0,
    )

    assert len(outcome.turns) == 6
    assert outcome.stop_reason == "completed 3 round(s)"


async def test_run_debate_timeout_stop_reason() -> None:
    """超时时 stop_reason 落 timeout（决策链路可观测）。"""

    async def _slow(*, system: str, user: str) -> None:
        await asyncio.sleep(5.0)

    llm = FakeLLMClient(
        {"you are a bull analyst": {"argument": "Bull says up"}},
        on_call=_slow,
    )

    outcome = await run_debate(
        bull=BullResearcher(llm=llm),
        bear=BearResearcher(llm=llm),
        venue="binance",
        symbol="BTC/USDT",
        timeframe="1h",
        as_of=_as_of(),
        briefs=[_brief("technical")],
        max_rounds=1,
        timeout_seconds=0.05,
    )

    assert outcome.turns == []
    assert outcome.stop_reason.startswith("timeout:")


def test_assess_disagreement_contested_on_confident_opposition() -> None:
    """有信心的多空对立 → contested；detail 不带前缀（前缀由 runner 组装）。"""
    contested, detail = assess_disagreement(
        [
            _brief("technical", "bullish", confidence=0.7),
            _brief("macro", "bearish", confidence=0.6),
            _brief("sentiment", "neutral", confidence=0.9),
        ]
    )
    assert contested is True
    assert "1 bullish vs 1 bearish" in detail
    assert not detail.startswith("contested:")  # 扁平契约：前缀只在 runner 加一层


def test_assess_disagreement_aligned_when_same_direction() -> None:
    """全员同向（或反方没信心）→ aligned，不值得辩。"""
    aligned_cases = [
        # 全 bullish
        [_brief("technical", "bullish", 0.8), _brief("macro", "bullish", 0.6)],
        # 反方存在但 confidence 低于门槛（失败 brief 的 0.0 也归于此）
        [_brief("technical", "bullish", 0.8), _brief("macro", "bearish", 0.1)],
        # 全 neutral
        [_brief("technical", "neutral", 0.9), _brief("macro", "neutral", 0.9)],
    ]
    for briefs in aligned_cases:
        contested, detail = assess_disagreement(briefs)
        assert contested is False
        assert "no confident opposing stances" in detail


def test_infer_asset_type_classifies_all_venues() -> None:
    """覆盖 5 类资产的 venue+symbol 路由。"""
    from inalpha_research.researchers.base import infer_asset_type

    # crypto
    assert infer_asset_type(venue="binance", symbol="BTC/USDT") == "crypto"
    assert infer_asset_type(venue="okx", symbol="ETH/USDT") == "crypto"
    # 美股
    assert infer_asset_type(venue="alpaca", symbol="AAPL") == "us_stock"
    assert infer_asset_type(venue="yfinance", symbol="AAPL") == "us_stock"
    assert infer_asset_type(venue="yfinance", symbol="SPY") == "us_stock"
    # A 股
    assert infer_asset_type(venue="akshare", symbol="sh.600519") == "cn_stock"
    assert infer_asset_type(venue="akshare", symbol="sz.000001") == "cn_stock"
    # 港股
    assert infer_asset_type(venue="akshare", symbol="hk.00700") == "hk_stock"
    # 日 / 英 / 德（akshare 归 global_stock）
    assert infer_asset_type(venue="akshare", symbol="jp.6758") == "global_stock"
    assert infer_asset_type(venue="akshare", symbol="uk.BARC") == "global_stock"
    # yfinance 后缀路由
    assert infer_asset_type(venue="yfinance", symbol="005930.KS") == "global_stock"  # 韩
    assert infer_asset_type(venue="yfinance", symbol="BHP.AX") == "global_stock"  # 澳
    assert infer_asset_type(venue="yfinance", symbol="^N225") == "global_stock"  # 指数
    # 未知 venue 兜底
    assert infer_asset_type(venue="unknown", symbol="X") == "global_stock"
