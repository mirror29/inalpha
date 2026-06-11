"""``POST/GET /strategy_candidates`` —— D-9 · LLM 自创策略候选池。

设计：

- ``POST`` 接收 LLM 写的源码字符串 → 跑三道沙盒 → 落表 → 返回 ``candidate_id``
- ``GET /strategy_candidates/{id}`` 返完整候选（含源码 / metrics / fitness）
- ``GET /strategy_candidates`` 列表（支持按 status / author_id 过滤；按 fitness DESC）
- ``POST /strategy_candidates/{id}/promote`` 把候选从 ``candidate`` 切到 ``promoted``

**审批门**：promote 暴露 HTTP，但 orchestration 端的 ``paper.promote_candidate`` tool
默认走 permission ``ask``——agent 调时由 permission engine 弹气泡让用户在对话里二次确认，
agent 不能自助。这是 ADR-0020 §关键约定 3 的精神：金融硬约束是"人保留最终决定权"，
而非"agent 物理上看不到 tool"。promote 后仅是状态切换，live trading runner 仍属 E2 / D-7。

跑回测复用 ``POST /backtest``，传 ``candidate_id`` 走候选分支；本路由不开"跑回测"端点。
"""
from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from inalpha_shared.auth import User, get_current_user
from inalpha_shared.db import DBConn
from inalpha_shared.errors import ConflictError, NotFoundError, ValidationError

from ..account_id import account_id_from_user
from ..schemas import (
    AuthorStrategyRequest,
    AuthorStrategyResponse,
    PromoteCandidateRequest,
    StrategyCandidateRecord,
    StrategyCandidateSummary,
)
from ..storage import strategy_candidates as candidates_store
from ..strategy_authoring import (
    ContractError,
    DynamicLoadError,
    audit_strategy_code,
    load_strategy_class,
    verify_strategy_contract,
)

router = APIRouter(tags=["strategy_candidates"])


@router.post("/strategy_candidates", response_model=AuthorStrategyResponse)
async def post_strategy_candidate(
    req: AuthorStrategyRequest,
    db: DBConn,
    user: Annotated[User, Depends(get_current_user)],
) -> AuthorStrategyResponse:
    """LLM / 用户提交策略源码 → 三道沙盒 → 落候选表。

    任何沙盒拒绝 → 422 + 详细 findings；LLM 应据此重写源码。
    撞到同 ``code_hash`` 的现有候选 → 返已有 ID（幂等，``created=False``）。
    """
    code = req.code

    # 1. AST 审计（main 进程）
    audit_result = audit_strategy_code(code)
    if not audit_result.ok:
        raise ValidationError(
            audit_result.reason(),
            code="STRATEGY_AUDIT_FAILED",
            details={
                "findings": [
                    {
                        "code": f.code,
                        "message": f.message,
                        "lineno": f.lineno,
                        "col_offset": f.col_offset,
                    }
                    for f in audit_result.findings
                ],
            },
        )

    # 2. dynamic_loader（受限 exec）—— 失败说明 LLM 写法不对（不是安全问题，但加载不了）
    try:
        cls = load_strategy_class(code)
    except DynamicLoadError as exc:
        raise ValidationError(
            f"策略源码无法加载：{exc}",
            code="STRATEGY_LOAD_FAILED",
        ) from exc

    # 3. 协议契约
    try:
        verify_strategy_contract(cls)
    except ContractError as exc:
        raise ValidationError(
            f"策略不符合协议契约：{exc}",
            code="STRATEGY_CONTRACT_FAILED",
        ) from exc

    # 4. 落表 —— author_id 仅当 user.user_id 是合法 UUID 时记录（兼容字符串 sub）；
    #    owner_account_id 走 account_id_from_user（与 strategy_runs 的 account_id 同源），
    #    供 live runner 起跑时做归属校验（issue #36.1）——非 UUID sub 也有稳定 owner。
    author_id = _try_uuid(user.user_id)
    owner_account_id = account_id_from_user(user)
    audit_dict = {
        "ok": True,
        "findings": [],
        "class_name": cls.__name__,
    }
    candidate_id, created = await candidates_store.insert_candidate(
        db,
        code=code,
        description=req.description,
        author="llm",  # MVP 一律标 llm；user 手写策略以后另开端点
        author_id=author_id,
        owner_account_id=owner_account_id,
        audit=audit_dict,
        factor_snapshot=req.factor_snapshot,
    )

    return AuthorStrategyResponse(
        candidate_id=candidate_id,
        code_hash=candidates_store.compute_code_hash(code),
        created=created,
        audit=audit_dict,
    )


@router.get(
    "/strategy_candidates/{candidate_id}",
    response_model=StrategyCandidateRecord,
)
async def get_strategy_candidate(
    candidate_id: UUID,
    db: DBConn,
    _user: Annotated[User, Depends(get_current_user)],
) -> StrategyCandidateRecord:
    """取候选完整内容（含完整源码 + 历次回测的最新 metrics）。"""
    row = await candidates_store.get_candidate(db, candidate_id)
    if row is None:
        raise NotFoundError(
            f"candidate {candidate_id} not found",
            code="CANDIDATE_NOT_FOUND",
        )
    return StrategyCandidateRecord(**row)


@router.get(
    "/strategy_candidates",
    response_model=list[StrategyCandidateSummary],
)
async def list_strategy_candidates(
    db: DBConn,
    _user: Annotated[User, Depends(get_current_user)],
    status: Annotated[str | None, Query(description="可选过滤 status")] = None,
    author_id: Annotated[
        UUID | None,
        Query(description="可选只看某用户创建的候选"),
    ] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[StrategyCandidateSummary]:
    """列候选（fitness DESC, NULLS LAST, created_at DESC）。

    不返回 ``code`` 字段省带宽；要看源码用 ``GET /strategy_candidates/{id}``。
    """
    rows = await candidates_store.list_candidates(
        db,
        status=status,
        author_id=author_id,
        limit=limit,
    )
    return [
        StrategyCandidateSummary(
            id=r["id"],
            code_hash=r["code_hash"],
            description=r["description"],
            author=r["author"],
            status=r["status"],
            metrics=r["metrics"],
            fitness=r["fitness"],
            last_backtest_run_id=r["last_backtest_run_id"],
            created_at=r["created_at"],
            updated_at=r["updated_at"],
        )
        for r in rows
    ]


@router.post(
    "/strategy_candidates/{candidate_id}/promote",
    response_model=StrategyCandidateRecord,
)
async def promote_strategy_candidate(
    candidate_id: UUID,
    req: PromoteCandidateRequest,
    db: DBConn,
    user: Annotated[User, Depends(get_current_user)],
) -> StrategyCandidateRecord:
    """把候选从 ``status='candidate'`` 切到 ``'promoted'``。

    审批门：orchestration 端 ``paper.promote_candidate`` tool 默认 permission ``ask``——
    agent 调时 permission engine 弹气泡让用户在对话里二次确认。本端点本身**不**做
    "调用者是 agent 还是人"的区分（鉴权由 JWT + service token 负责）。

    校验：

    - 候选不存在 → 404 ``CANDIDATE_NOT_FOUND``
    - 当前 status ≠ ``candidate``（已 promoted / rejected）→ 409 ``CANDIDATE_NOT_PROMOTABLE``
    - ``fitness IS NULL``（没跑过回测）→ 422 ``CANDIDATE_NOT_BACKTESTED``——必须先跑
      ``POST /backtest`` 拿到 fitness，避免误把没验证的策略推上线

    成功：把 ``status='promoted'``，``audit.promotion`` 写 ``{reason, promoted_by, promoted_at}``。
    """
    row = await candidates_store.get_candidate(db, candidate_id)
    if row is None:
        raise NotFoundError(
            f"candidate {candidate_id} not found",
            code="CANDIDATE_NOT_FOUND",
        )

    current_status = row["status"]
    if current_status != "candidate":
        raise ConflictError(
            f"candidate {candidate_id} is in status {current_status!r}; "
            "only 'candidate' rows can be promoted",
            code="CANDIDATE_NOT_PROMOTABLE",
            details={"current_status": current_status},
        )

    if row["fitness"] is None:
        raise ValidationError(
            f"candidate {candidate_id} has no fitness (never backtested); "
            "run POST /backtest with candidate_id first to compute fitness",
            code="CANDIDATE_NOT_BACKTESTED",
        )

    await candidates_store.promote_candidate(
        db,
        candidate_id,
        reason=req.reason,
        promoted_by=user.user_id,
    )

    updated = await candidates_store.get_candidate(db, candidate_id)
    # promote 函数保证行存在；updated is None 不可达，但 mypy 仍要求 narrow
    assert updated is not None, "candidate disappeared mid-transaction"
    return StrategyCandidateRecord(**updated)


def _try_uuid(s: str) -> UUID | None:
    """字符串 → UUID 或 None（兼容非 UUID 的 sub claim）。"""
    try:
        return UUID(s)
    except (ValueError, TypeError):
        return None
