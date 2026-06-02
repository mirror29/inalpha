"""辩论协调器 —— 轮换调 Bull / Bear，输出 ``list[DebateTurn]``。

终止条件：完成 ``max_rounds`` 轮（每轮 = Bull 一次 + Bear 一次）。
0 轮直接返空列表（runner 在 ``settings.max_debate_rounds=0`` 时不会调本函数）。

容错 / 性能（D-10）：

- **单轮失败兜底**（`_safe_speak`）：单个 researcher LLM 抽风一次不让整条 deep_dive
  500，失败那轮 content 落 "(researcher failed: <err>)"，manager 仍能继续。
- **#1 开场并行**：``max_rounds >= 2`` 时第 1 轮 Bull/Bear 是**独立开场**（互不读对方），
  并行跑省一次串行延迟；第 2 轮起才是"看对方上一轮再反驳"的串行 rebuttal。
  ``max_rounds == 1`` 时保持串行（Bull 开场 → Bear 反驳），不牺牲那唯一一次反驳的价值。
- **#2 输出限长**：``max_tokens`` 透传到每次发言（默认调小，论证更紧凑、延迟更低）。
- **#4 总时限**：``timeout_seconds`` 限整个辩论阶段；超时返回**已完成**的部分 log，
  不抛、不拖满下游 tool 预算（配合 manager 兜底保证 deep_dive 端到端不挂）。
"""
from __future__ import annotations

import asyncio
from datetime import datetime

from inalpha_shared import get_logger

from .researchers import BearResearcher, BullResearcher
from .schemas import AnalystBrief, DebateTurn

_logger = get_logger(__name__)


async def run_debate(
    *,
    bull: BullResearcher,
    bear: BearResearcher,
    venue: str,
    symbol: str,
    timeframe: str,
    as_of: datetime,
    briefs: list[AnalystBrief],
    max_rounds: int,
    max_tokens: int = 2048,
    timeout_seconds: float | None = None,
) -> list[DebateTurn]:
    """跑 ``max_rounds`` 轮 Bull/Bear 对喷，返回完整 debate log。

    Args:
        max_rounds: 轮数；<= 0 直接返空列表。
        max_tokens: 每次发言输出上限（#2）。
        timeout_seconds: 整个辩论阶段总时限（#4）；None = 不限时。超时返回部分 log。

    Returns:
        ``list[DebateTurn]`` 按发言顺序：[R1-Bull, R1-Bear, R2-Bull, R2-Bear, ...]
        （即使第 1 轮并行执行，落 log 顺序仍固定 Bull 在前。）
    """
    if max_rounds <= 0:
        return []

    log: list[DebateTurn] = []

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
                continue

            # 串行：Bull 先发言（max_rounds==1 时即开场；>=2 时为 rebuttal 轮）
            bull_text = await _safe_speak(
                researcher=bull,
                venue=venue,
                symbol=symbol,
                timeframe=timeframe,
                as_of=as_of,
                briefs=briefs,
                history=log,
                round_no=r,
                max_tokens=max_tokens,
            )
            log.append(DebateTurn(role="bull", round=r, content=bull_text))

            # Bear 看到 Bull 的发言后再回（rebut）
            bear_text = await _safe_speak(
                researcher=bear,
                venue=venue,
                symbol=symbol,
                timeframe=timeframe,
                as_of=as_of,
                briefs=briefs,
                history=log,
                round_no=r,
                max_tokens=max_tokens,
            )
            log.append(DebateTurn(role="bear", round=r, content=bear_text))

    if timeout_seconds is None:
        await _run_all_rounds()
        return log

    # #4 总时限：超时返回已完成的部分 log（in-flight 那轮被取消、不落 log）
    try:
        async with asyncio.timeout(timeout_seconds):
            await _run_all_rounds()
    except TimeoutError:
        _logger.warning(
            "debate_timeout",
            symbol=symbol,
            timeout_seconds=timeout_seconds,
            completed_turns=len(log),
        )
    return log


async def _safe_speak(
    *,
    researcher: BullResearcher | BearResearcher,
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
