"""把 ``DeepDiveRequest`` 翻成 analysts → manager → ``ResearchPlan`` 的胶水。

把"实例化 analyst / 并行调 LLM / 综合"的所有粘合代码集中在这里，让 api 层薄。
"""
from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from .analysts import ALL_ANALYSTS
from .manager import ResearchManager
from .schemas import AnalystBrief, DeepDiveRequest, ResearchPlan

if TYPE_CHECKING:
    from .data_client import DataClient
    from .llm.client import LLMClient


async def run_deep_dive(
    req: DeepDiveRequest,
    *,
    llm: LLMClient,
    data: DataClient,
) -> ResearchPlan:
    """执行一次完整 deep dive：所有 analyst 并行 → manager 综合。

    实现注：

    - analyst 之间是 ``asyncio.gather`` 并行 —— LLM 调用是 IO bound，并行能省总时长
    - 单个 analyst 失败用 ``return_exceptions=True`` 不阻断整链；fail 的 brief
      被替换成 ``_failed_brief()`` 标记，manager 仍能综合可见信息
    - **review B13 fix**：``isinstance(result, Exception)`` 而非 BaseException —— 后者
      会把 KeyboardInterrupt / SystemExit 也吞成 neutral brief，dev Ctrl-C 不响应；
      Py 3.11+ ``gather(return_exceptions=True)`` 本就不捕 BaseException，写它是误导
    """
    analysts = [cls(llm=llm, data=data) for cls in ALL_ANALYSTS]

    coros = [
        a.run(
            venue=req.venue,
            symbol=req.symbol,
            timeframe=req.timeframe,
            as_of=req.as_of,
            lookback_days=req.lookback_days,
        )
        for a in analysts
    ]
    results = await asyncio.gather(*coros, return_exceptions=True)

    briefs: list[AnalystBrief] = []
    for analyst, result in zip(analysts, results, strict=True):
        if isinstance(result, Exception):
            briefs.append(_failed_brief(analyst.type_id, repr(result)))
        else:
            briefs.append(result)

    manager = ResearchManager(llm=llm)
    plan = await manager.synthesize(
        venue=req.venue,
        symbol=req.symbol,
        timeframe=req.timeframe,
        as_of=req.as_of,
        briefs=briefs,
        user_question=req.user_question,
    )
    return plan


_VALID_ANALYST_TYPES = ("technical", "fundamental", "sentiment", "risk", "macro")


def _failed_brief(analyst_type: str, error: str) -> AnalystBrief:
    """analyst 失败时塞一个 neutral / low-confidence brief 给 manager。"""
    # 限制 analyst 字段值（schema literal）；未识别类型回落到 'technical' 防爆
    type_id = analyst_type if analyst_type in _VALID_ANALYST_TYPES else "technical"
    return AnalystBrief(
        analyst=type_id,  # type: ignore[arg-type]
        stance="neutral",
        confidence=0.0,
        summary=f"(analyst failed) {error[:200]}",
        key_points=[],
        raw_excerpt=None,
    )
