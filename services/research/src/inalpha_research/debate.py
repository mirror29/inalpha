"""辩论协调器 —— 轮换调 Bull / Bear（/ Risk），输出 ``DebateOutcome``。

终止条件（research-hub #6 起三选一，落 ``DebateOutcome.stop_reason``）：

- **completed**：跑满 ``max_rounds`` 轮（每轮 = Bull + Bear（+ Risk）各一次）
- **converged 软早停**：从第 2 轮起，若 Bull 与 Bear 本轮论证与各自上一轮的
  词汇重合度（Jaccard）都 ≥ ``convergence_threshold``，视为没有新论点，提前
  结束省 token（阈值 1.0 = 实际禁用，只有逐字相同才触发）
- **timeout**：整段超时返回**已完成**的部分 log（in-flight 那轮被取消）

0 轮直接返空 outcome（runner 在 ``settings.max_debate_rounds=0`` 时不会调本函数）。

容错 / 性能（D-10）：

- **单轮失败兜底**（`_safe_speak`）：单个 researcher LLM 抽风一次不让整条 deep_dive
  500，失败那轮 content 落 "(researcher failed: <err>)"，manager 仍能继续。
- **#1 开场并行**：``max_rounds >= 2`` 时第 1 轮 Bull/Bear 是**独立开场**（互不读对方），
  并行跑省一次串行延迟；Risk（如启用）在两者之后串行发言（它必须读到双方开场）。
  ``max_rounds == 1`` 时保持串行（Bull 开场 → Bear 反驳 → Risk 压测），
  不牺牲那唯一一次反驳的价值。
- **#2 输出限长**：``max_tokens`` 透传到每次发言（默认调小，论证更紧凑、延迟更低）。
- **#4 总时限**：``timeout_seconds`` 限整个辩论阶段；超时返回**已完成**的部分 log，
  不抛、不拖满下游 tool 预算（配合 manager 兜底保证 deep_dive 端到端不挂）。
"""
from __future__ import annotations

import asyncio
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime

from inalpha_shared import get_logger

from .researchers import Researcher
from .schemas import AnalystBrief, DebateTurn

_logger = get_logger(__name__)


@dataclass
class DebateOutcome:
    """一次辩论的完整结果：发言序列 + 为什么停（决策链路可观测，#6）。"""

    turns: list[DebateTurn] = field(default_factory=list)
    stop_reason: str = "completed"


def assess_disagreement(
    briefs: list[AnalystBrief],
    *,
    min_confidence: float = 0.35,
) -> tuple[bool, str]:
    """briefs 分歧度判定 —— 争议大才值得花 token 辩论（research-hub #6）。

    规则（确定性，不走 LLM）：同时存在 confidence ≥ ``min_confidence`` 的
    bullish 与 bearish brief 即视为 contested。全员同向 / 全员 neutral /
    反方都没信心 → aligned，跳过辩论。

    Returns:
        ``(contested, detail)``——detail 是**不带前缀**的判定描述（英文，机器/
        日志语境；面向用户的语言由 orchestrator 按用户语言呈现）。runner 据此
        组装 ``ResearchPlan.debate_trigger``，前缀固定三选一
        ``contested: / skipped: / always:``（PR #81 CR：扁平契约，下游可安全
        ``startswith`` 解析）。
    """
    bulls = [b for b in briefs if b.stance == "bullish" and b.confidence >= min_confidence]
    bears = [b for b in briefs if b.stance == "bearish" and b.confidence >= min_confidence]
    if bulls and bears:
        return True, (
            f"{len(bulls)} bullish vs {len(bears)} bearish analysts "
            f"(confidence >= {min_confidence:.2f})"
        )
    stance_mix = dict(Counter(b.stance for b in briefs))
    return False, f"no confident opposing stances (stance mix: {stance_mix})"


async def run_debate(
    *,
    bull: Researcher,
    bear: Researcher,
    risk: Researcher | None = None,
    venue: str,
    symbol: str,
    timeframe: str,
    as_of: datetime,
    briefs: list[AnalystBrief],
    max_rounds: int,
    max_tokens: int = 2048,
    timeout_seconds: float | None = None,
    convergence_threshold: float = 1.0,
) -> DebateOutcome:
    """跑至多 ``max_rounds`` 轮辩论，返回 ``DebateOutcome``。

    Args:
        risk: 风险官（research-hub #6 三方制）；None = 维持 Bull/Bear 两方。
        max_rounds: 轮数；<= 0 直接返空 outcome。
        max_tokens: 每次发言输出上限（#2）。
        timeout_seconds: 整个辩论阶段总时限（#4）；None = 不限时。超时返回部分 log。
        convergence_threshold: 软早停阈值 [0,1]；从第 2 轮起 Bull/Bear 与各自上轮
            的 Jaccard 重合度都 ≥ 此值则提前停。1.0 = 实际禁用。

    Returns:
        ``DebateOutcome``——``turns`` 按发言顺序：[R1-Bull, R1-Bear, (R1-Risk),
        R2-Bull, ...]（即使第 1 轮 Bull/Bear 并行执行，落 log 顺序仍固定 Bull 在前）。
    """
    if max_rounds <= 0:
        return DebateOutcome(turns=[], stop_reason="skipped: max_rounds <= 0")

    outcome = DebateOutcome()
    log = outcome.turns

    async def _speak_and_log(researcher: Researcher, r: int) -> str:
        text = await _safe_speak(
            researcher=researcher,
            venue=venue,
            symbol=symbol,
            timeframe=timeframe,
            as_of=as_of,
            briefs=briefs,
            history=log,
            round_no=r,
            max_tokens=max_tokens,
        )
        log.append(DebateTurn(role=researcher.role, round=r, content=text))
        return text

    async def _run_all_rounds() -> None:
        for r in range(1, max_rounds + 1):
            if r == 1 and max_rounds >= 2:
                # #1 开场并行：多轮时第 1 轮只是独立开场（rebuttal 在后续轮），
                # Bull/Bear 都基于空 history 并行发言，省一次串行延迟。
                bull_text, bear_text = await asyncio.gather(
                    _safe_speak(
                        researcher=bull,
                        venue=venue,
                        symbol=symbol,
                        timeframe=timeframe,
                        as_of=as_of,
                        briefs=briefs,
                        history=[],
                        round_no=r,
                        max_tokens=max_tokens,
                    ),
                    _safe_speak(
                        researcher=bear,
                        venue=venue,
                        symbol=symbol,
                        timeframe=timeframe,
                        as_of=as_of,
                        briefs=briefs,
                        history=[],
                        round_no=r,
                        max_tokens=max_tokens,
                    ),
                )
                # 落 log 顺序固定 Bull 在前（与完成顺序无关）
                log.append(DebateTurn(role="bull", round=r, content=bull_text))
                log.append(DebateTurn(role="bear", round=r, content=bear_text))
            else:
                # 串行：Bull 先发言（max_rounds==1 时即开场；>=2 时为 rebuttal 轮），
                # Bear 看到 Bull 后再回（rebut）
                await _speak_and_log(bull, r)
                await _speak_and_log(bear, r)

            # Risk 风险官压测双方——必须读到本轮 Bull/Bear 之后才发言，永远串行殿后
            if risk is not None:
                await _speak_and_log(risk, r)

            # 软早停（#6）：本轮与上一轮 Bull/Bear 各自重合度都过阈 = 没有新论点。
            # 只在还有下一轮时才检查——最后一轮停不停都一样，别污染 completed 语义。
            if r >= 2 and r < max_rounds and _converged(log, r, convergence_threshold):
                outcome.stop_reason = (
                    f"converged: round {r} bull/bear arguments overlap >= "
                    f"{convergence_threshold:.2f} with round {r - 1} "
                    f"(stopped {max_rounds - r} round(s) early)"
                )
                _logger.info(
                    "debate_converged",
                    symbol=symbol,
                    round=r,
                    max_rounds=max_rounds,
                    threshold=convergence_threshold,
                )
                return

        outcome.stop_reason = f"completed {max_rounds} round(s)"

    if timeout_seconds is None:
        await _run_all_rounds()
        return outcome

    # #4 总时限：超时返回已完成的部分 log（in-flight 那轮被取消、不落 log）
    try:
        async with asyncio.timeout(timeout_seconds):
            await _run_all_rounds()
    except TimeoutError:
        outcome.stop_reason = (
            f"timeout: {timeout_seconds:.0f}s elapsed after {len(log)} turn(s)"
        )
        _logger.warning(
            "debate_timeout",
            symbol=symbol,
            timeout_seconds=timeout_seconds,
            completed_turns=len(log),
        )
    return outcome


def _converged(log: list[DebateTurn], round_no: int, threshold: float) -> bool:
    """Bull 与 Bear 本轮 vs 上轮的词汇 Jaccard 都 ≥ threshold 即收敛。

    纯词面启发式（零 LLM 开销）：辩论无新论点时双方会复述既有词汇，重合度高；
    阈值 ≥ 1.0 直接短路禁用。Risk 轮不参与判定——它每轮压测对象不同，天然多变。
    """
    if threshold >= 1.0:
        return False
    for role in ("bull", "bear"):
        cur = next((t.content for t in log if t.role == role and t.round == round_no), None)
        prev = next(
            (t.content for t in log if t.role == role and t.round == round_no - 1), None
        )
        if cur is None or prev is None:
            return False
        if _jaccard(cur, prev) < threshold:
            return False
    return True


def _jaccard(a: str, b: str) -> float:
    """小写分词集合的 Jaccard 相似度；空文本视为 0（永不触发收敛）。"""
    ta, tb = set(a.lower().split()), set(b.lower().split())
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


async def _safe_speak(
    *,
    researcher: Researcher,
    venue: str,
    symbol: str,
    timeframe: str,
    as_of: datetime,
    briefs: list[AnalystBrief],
    history: list[DebateTurn],
    round_no: int,
    max_tokens: int = 2048,
) -> str:
    """单轮发言；LLM 抛错时返带 ``(researcher failed)`` 前缀的字串，不中断辩论。

    注：只吞 ``Exception``——``asyncio.CancelledError`` 是 ``BaseException``（超时取消用），
    不会被这里吞掉，会正常向上传给 #4 的 ``asyncio.timeout``。
    """
    try:
        return await researcher.speak(
            venue=venue,
            symbol=symbol,
            timeframe=timeframe,
            as_of=as_of,
            briefs=briefs,
            history=history,
            round_no=round_no,
            max_tokens=max_tokens,
        )
    except Exception as e:
        _logger.warning(
            "researcher_failed",
            role=researcher.role,
            round=round_no,
            error=repr(e),
        )
        return f"(researcher failed: {e!r})"
