"""辩论协调器 —— 轮换调 Bull / Bear，输出 ``list[DebateTurn]``。

终止条件：完成 ``max_rounds`` 轮（每轮 = Bull 一次 + Bear 一次）。
0 轮直接返空列表（runner 在 ``settings.max_debate_rounds=0`` 时不会调本函数）。

单个 researcher 失败用 try/except 包住——LLM 抽风一次不应让整条 deep_dive 500，
失败那轮的 content 落"(researcher failed: <err>)" 字串，manager 仍能继续。
"""
from __future__ import annotations

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
) -> list[DebateTurn]:
    """跑 ``max_rounds`` 轮 Bull/Bear 对喷，返回完整 debate log。

    Args:
        max_rounds: 轮数；<= 0 直接返空列表。

    Returns:
        ``list[DebateTurn]`` 按发言顺序：[R1-Bull, R1-Bear, R2-Bull, R2-Bear, ...]
    """
    if max_rounds <= 0:
        return []

    log: list[DebateTurn] = []
    for r in range(1, max_rounds + 1):
        # Bull 先发言
        bull_text = await _safe_speak(
            researcher=bull,
            venue=venue,
            symbol=symbol,
            timeframe=timeframe,
            as_of=as_of,
            briefs=briefs,
            history=log,
            round_no=r,
        )
        log.append(DebateTurn(role="bull", round=r, content=bull_text))

        # Bear 看到 Bull 的发言后再回
        bear_text = await _safe_speak(
            researcher=bear,
            venue=venue,
            symbol=symbol,
            timeframe=timeframe,
            as_of=as_of,
            briefs=briefs,
            history=log,
            round_no=r,
        )
        log.append(DebateTurn(role="bear", round=r, content=bear_text))

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
) -> str:
    """单轮发言；LLM 抛错时返带 ``(researcher failed)`` 前缀的字串，不中断辩论。"""
    try:
        return await researcher.speak(
            venue=venue,
            symbol=symbol,
            timeframe=timeframe,
            as_of=as_of,
            briefs=briefs,
            history=history,
            round_no=round_no,
        )
    except Exception as e:  # noqa: BLE001 — 故意宽捕，落败也要让辩论继续
        _logger.warning(
            "researcher_failed",
            role=researcher.role,
            round=round_no,
            error=repr(e),
        )
        return f"(researcher failed: {e!r})"
