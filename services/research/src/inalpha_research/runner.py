"""把 ``DeepDiveRequest`` 翻成 analysts → debate → manager → ``ResearchPlan`` 的胶水。

D-9 起新增 Bull/Bear 辩论阶段（``settings.max_debate_rounds`` 控制）：

1. 核心 analyst 并行出 ``AnalystBrief``（ADR-0037 §A：若 ``req.personas`` 指定，
   再追加对应投资大师人格 analyst，一并并行）
2. **Bull/Bear 辩论 N 轮**（N=0 时跳过，保留旧 D-8c 直连行为）
3. Manager 综合 briefs + debate_log → ``ResearchPlan``

把"实例化 analyst / 并行调 LLM / 辩论 / 综合"的所有粘合代码集中在这里，让 api 层薄。
"""
from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, get_args

from .analysts import ALL_ANALYSTS
from .analysts.personas import PERSONA_ANALYSTS
from .config import get_research_settings
from .debate import run_debate
from .manager import ResearchManager
from .researchers import BearResearcher, BullResearcher
from .schemas import AnalystBrief, DebateTurn, DeepDiveRequest, ResearchPlan

if TYPE_CHECKING:
    from .data_client import DataClient
    from .llm.client import LLMClient


async def run_deep_dive(
    req: DeepDiveRequest,
    *,
    llm: LLMClient,
    data: DataClient,
) -> ResearchPlan:
    """执行一次完整 deep dive：analysts 并行 → 辩论 → manager 综合。

    实现注：

    - analyst 之间是 ``asyncio.gather`` 并行 —— LLM 调用是 IO bound，并行能省总时长
    - 单个 analyst 失败用 ``return_exceptions=True`` 不阻断整链；fail 的 brief
      被替换成 ``_failed_brief()`` 标记，manager 仍能综合可见信息
    - 辩论是**串行**的（Bull 先看完 briefs 发言，Bear 看了 Bull 再回喷）；
      ``max_debate_rounds=0`` 时整段跳过，行为退化到 D-8c 的直连综合
    - **review B13 fix**：``isinstance(result, Exception)`` 而非 BaseException —— 后者
      会把 KeyboardInterrupt / SystemExit 也吞成 neutral brief，dev Ctrl-C 不响应；
      Py 3.11+ ``gather(return_exceptions=True)`` 本就不捕 BaseException，写它是误导
    """
    settings = get_research_settings()

    # ─── 1) analyst 并行 ────────────────────────────────────────────
    # 核心 analyst 永远跑；ADR-0037 §A：req.personas 指定的投资大师人格按需追加
    # （无效 key 静默忽略，不阻断主链路）。
    analyst_classes = list(ALL_ANALYSTS)
    for key in req.personas or []:
        persona_cls = PERSONA_ANALYSTS.get(key)
        if persona_cls is not None:
            analyst_classes.append(persona_cls)
    analysts = [cls(llm=llm, data=data) for cls in analyst_classes]

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

    # ─── 2) Bull/Bear 辩论（max_debate_rounds=0 跳过）───────────────
    debate_log: list[DebateTurn] = []
    if settings.max_debate_rounds > 0:
        bull = BullResearcher(llm=llm)
        bear = BearResearcher(llm=llm)
        debate_log = await run_debate(
            bull=bull,
            bear=bear,
            venue=req.venue,
            symbol=req.symbol,
            timeframe=req.timeframe,
            as_of=req.as_of,
            briefs=briefs,
            max_rounds=settings.max_debate_rounds,
        )

    # ─── 3) Manager 综合 ────────────────────────────────────────────
    manager = ResearchManager(llm=llm)
    plan = await manager.synthesize(
        venue=req.venue,
        symbol=req.symbol,
        timeframe=req.timeframe,
        as_of=req.as_of,
        briefs=briefs,
        debate_log=debate_log,
        user_question=req.user_question,
    )
    return plan


# 从 AnalystBrief.analyst Literal 动态派生合法类型集 —— 一劳永逸消除"加了 analyst /
# persona 忘了同步这个元组"的 drift bug（历史上 valuation 就曾漏在这里）。
_VALID_ANALYST_TYPES: tuple[str, ...] = get_args(
    AnalystBrief.model_fields["analyst"].annotation
)


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
