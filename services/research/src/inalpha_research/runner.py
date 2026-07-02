"""把 ``DeepDiveRequest`` 翻成 analysts → debate → manager → ``ResearchPlan`` 的胶水。

D-9 起新增辩论阶段（``settings.max_debate_rounds`` 控制），research-hub #6 升级三方制：

1. 核心 analyst 并行出 ``AnalystBrief``（ADR-0037 §A：若 ``req.personas`` 指定，
   再追加对应投资大师人格 analyst，一并并行）
2. **Bull/Bear(/Risk) 辩论 ≤N 轮**（N=0 跳过；默认「争议触发」——briefs 多空对立
   才辩，软早停见 debate.run_debate；触发判定与终止原因落 plan 供复盘）
3. Manager 综合 briefs + debate_log → ``ResearchPlan``（含 synthesis_reasoning）

把"实例化 analyst / 并行调 LLM / 辩论 / 综合"的所有粘合代码集中在这里，让 api 层薄。
"""
from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import TYPE_CHECKING, Any, get_args

from inalpha_shared import get_logger

from .analysts import ALL_ANALYSTS
from .analysts.base import AnalystContext, factor_lookback_bars
from .analysts.personas import PERSONA_ANALYSTS
from .config import get_research_settings
from .debate import assess_disagreement, run_debate
from .manager import ResearchManager
from .researchers import BearResearcher, BullResearcher, RiskResearcher
from .schemas import AnalystBrief, DebateTurn, DeepDiveRequest, ResearchPlan

_logger = get_logger(__name__)

if TYPE_CHECKING:
    from .data_client import DataClient
    from .factor_client import FactorClient
    from .llm.client import LLMClient


async def _prefetch_shared(
    req: DeepDiveRequest,
    *,
    data: DataClient,
    factor: FactorClient | None,
) -> AnalystContext | None:
    """D-13 · P0：一次预拉 K 线 + 因子快照，注入 technical analyst 复用。

    每项独立容错（gather return_exceptions）：失败的那项回退 None，
    对应 analyst 会在 build_user_prompt 里自己拉。全挂则返回 None。

    **不预取 fundamentals**：fundamental/valuation analyst 用的是
    ``fundamentals_route`` 路由后的 fund_venue（可能 ≠ 研究 venue），
    预取的 req.venue 版本对不上它们的需求——接进去反而喂错数据源。
    预取范围因此限于 bars + factor_snapshot（消费方明确 = technical）。
    """
    from_ts = req.as_of - timedelta(days=req.lookback_days)

    async def _bars() -> list[dict[str, Any]]:
        return await data.get_bars(
            venue=req.venue, symbol=req.symbol, timeframe=req.timeframe,
            from_ts=from_ts, to_ts=req.as_of, limit=2_000,
        )

    async def _factor() -> dict[str, Any] | None:
        if factor is None:
            return None
        return await factor.get_snapshot(
            venue=req.venue, symbol=req.symbol, timeframe=req.timeframe,
            as_of=req.as_of, lookback_bars=factor_lookback_bars(req.lookback_days),
        )

    pre_bars, pre_factor = await asyncio.gather(
        _bars(), _factor(), return_exceptions=True,
    )
    return AnalystContext(
        bars=None if isinstance(pre_bars, BaseException) else pre_bars,
        factor_snapshot=(
            None if isinstance(pre_factor, BaseException) else pre_factor
        ),
    )


async def run_deep_dive(
    req: DeepDiveRequest,
    *,
    llm: LLMClient,
    data: DataClient,
    factor: FactorClient | None = None,
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

    # ─── 0) 数据预取（D-13 · P0）────────────────────────────────────
    # 6 个 analyst 各自调 DataClient 拉同一批 K 线 → N 次重复往返。
    # 一次预拉后注入所有 analyst，延迟 -30%、服务端负载 -60%。
    # 单个预取失败不阻断整链：回退为 None，analyst 在 build_user_prompt 里自己拉。
    shared = await _prefetch_shared(req, data=data, factor=factor)

    # ─── 1) analyst 并行 ────────────────────────────────────────────
    # 核心 analyst 永远跑；ADR-0037 §A：req.personas 指定的投资大师人格按需追加
    # （无效 key 静默忽略，不阻断主链路）。
    # 去重保序：重复 persona key 否则会追加多个同类 analyst → 产出多条相同 brief，
    # manager 综合时该视角被人为加权、且多耗 LLM 调用（dict.fromkeys 保留首次出现顺序）。
    analyst_classes = list(ALL_ANALYSTS)
    for key in dict.fromkeys(req.personas or []):
        persona_cls = PERSONA_ANALYSTS.get(key)
        if persona_cls is not None:
            analyst_classes.append(persona_cls)
    analysts = [cls(llm=llm, data=data, factor=factor, shared=shared) for cls in analyst_classes]

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

    # ─── 2) Bull/Bear(/Risk) 辩论（max_debate_rounds=0 跳过）────────
    # research-hub #6：默认「争议触发」——analyst 同向时辩论只是复读 briefs，
    # 跳过省 token；判定结果 + 终止原因落 plan 供复盘（决策链路可观测）。
    debate_log: list[DebateTurn] = []
    debate_trigger: str | None = None
    debate_stop_reason: str | None = None
    if settings.max_debate_rounds > 0:
        contested, detail = assess_disagreement(
            briefs, min_confidence=settings.debate_min_confidence
        )
        should_debate = settings.debate_trigger == "always" or contested
        # 前缀固定三选一（contested:/skipped:/always:），扁平结构供下游 startswith 解析
        debate_trigger = (
            f"always: debate forced regardless of disagreement ({detail})"
            if settings.debate_trigger == "always"
            else f"contested: {detail}" if contested
            else f"skipped: {detail}"
        )
        _logger.info(
            "debate_trigger",
            symbol=req.symbol,
            trigger_mode=settings.debate_trigger,
            contested=contested,
            should_debate=should_debate,
        )
        if should_debate:
            outcome = await run_debate(
                bull=BullResearcher(llm=llm),
                bear=BearResearcher(llm=llm),
                risk=RiskResearcher(llm=llm) if settings.debate_risk_enabled else None,
                venue=req.venue,
                symbol=req.symbol,
                timeframe=req.timeframe,
                as_of=req.as_of,
                briefs=briefs,
                max_rounds=settings.max_debate_rounds,
                # #2 限输出长度 + #4 总时限（debate.run_debate 内部超时返部分 log）
                max_tokens=settings.debate_max_tokens,
                timeout_seconds=settings.debate_timeout_seconds,
                convergence_threshold=settings.debate_convergence_threshold,
            )
            debate_log = outcome.turns
            debate_stop_reason = outcome.stop_reason

    # ─── 3) Manager 综合 ─
    # 注：prompt 侧的 token 压缩（key_points 截断）在 manager._format_user_prompt
    # 内完成，不在此处改 briefs——runner 直接把完整 briefs 传下去，保证返回给
    # 调用方的 ResearchPlan.briefs 保留 raw_excerpt（debug/复盘用）与完整 key_points。
    manager = ResearchManager(llm=llm)
    plan = await manager.synthesize(
        venue=req.venue,
        symbol=req.symbol,
        timeframe=req.timeframe,
        as_of=req.as_of,
        briefs=briefs,
        debate_log=debate_log,
        user_question=req.user_question,
        debate_trigger=debate_trigger,
        debate_stop_reason=debate_stop_reason,
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
