"""``/candidates`` —— 因子候选池：propose / list / review（D-12 · 因子发现 L1）。

- ``POST /candidates``：agent 经 factor.propose 落候选（表达式先过白名单审计）
- ``GET /candidates``：列候选（dashboard 审核页 + factor.list_candidates）
- ``POST /candidates/{id}/review``：**人工**审核 → registered / rejected。
  此端点**不挂任何 LLM tool**（register 门，ADR-0019 精神的更硬实现）；
  registered 后立即刷新 custom 注册表，新因子秒进 catalog/timing/score。

DB 不可用（lifespan 连接失败）→ 全部 503 ``FACTOR_DB_UNAVAILABLE``；
timing/score/catalog 不受影响（factor 服务保持可无 DB 启动）。
"""
from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Path, Query, Request
from inalpha_shared.db import get_conn
from inalpha_shared.errors import ConflictError, InalphaError, NotFoundError, ValidationError

from .. import custom_registry
from ..expression import ExpressionError, parse_expression
from ..schemas import (
    FactorCandidateRecord,
    ProposeFactorRequest,
    ProposeFactorResponse,
    ReviewFactorCandidateRequest,
)
from ..storage import candidates as candidates_store

router = APIRouter(tags=["factor_candidates"])


class FactorDbUnavailableError(InalphaError):
    code = "FACTOR_DB_UNAVAILABLE"
    status_code = 503


def _require_db(request: Request) -> None:
    if not getattr(request.app.state, "db_ready", False):
        raise FactorDbUnavailableError(
            "factor service has no database connection; candidates API is unavailable "
            "(timing/score/catalog still work)"
        )


@router.post("/candidates", response_model=ProposeFactorResponse)
async def propose_candidate(
    req: ProposeFactorRequest, request: Request
) -> ProposeFactorResponse:
    """提候选：表达式再过一遍白名单审计（防绕过 /custom/score 直接 propose 垃圾）。

    审计在 DB 检查之前——表达式非法时即使 DB 不可用也给出可改写的 400，而非 503。
    """
    try:
        parse_expression(req.expression)
    except ExpressionError as exc:
        raise ValidationError(
            f"表达式未通过审计：{exc}", code="FACTOR_EXPRESSION_INVALID"
        ) from exc
    _require_db(request)

    async with get_conn() as conn:
        candidate_id, created = await candidates_store.insert_candidate(
            conn,
            expression=req.expression,
            hypothesis=req.hypothesis,
            name=req.name,
            proposed_by=req.proposed_by,
            venue=req.venue,
            symbol=req.symbol,
            timeframe=req.timeframe,
            test_results=req.test_results,
            batch_id=req.batch_id,
            n_tested=req.n_tested,
        )
    return ProposeFactorResponse(
        candidate_id=candidate_id,
        expression_hash=candidates_store.compute_expression_hash(req.expression),
        created=created,
    )


@router.get("/candidates", response_model=list[FactorCandidateRecord])
async def list_candidates(
    request: Request,
    status: Annotated[str | None, Query(description="pending_review/registered/rejected")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[FactorCandidateRecord]:
    _require_db(request)
    async with get_conn() as conn:
        rows = await candidates_store.list_candidates(conn, status=status, limit=limit)
    return [FactorCandidateRecord(**r) for r in rows]


@router.post("/candidates/{candidate_id}/review", response_model=FactorCandidateRecord)
async def review_candidate(
    candidate_id: Annotated[UUID, Path()],
    req: ReviewFactorCandidateRequest,
    request: Request,
) -> FactorCandidateRecord:
    """人工审核（dashboard 直调；agent 物理上没有此 tool）。"""
    _require_db(request)
    async with get_conn() as conn:
        row = await candidates_store.review(
            conn, candidate_id,
            action=req.action, reviewed_by=req.reviewed_by, note=req.note,
        )
        if row is None:
            existing = await candidates_store.get_candidate(conn, candidate_id)
            if existing is None:
                raise NotFoundError(
                    f"factor candidate {candidate_id} not found",
                    code="FACTOR_CANDIDATE_NOT_FOUND",
                )
            raise ConflictError(
                f"factor candidate {candidate_id} is '{existing['status']}'; "
                "only pending_review rows can be reviewed",
                code="FACTOR_CANDIDATE_NOT_REVIEWABLE",
                details={"current_status": existing["status"]},
            )
    # 注册秒生效（reject 也刷新——幂等，开销一次查询）
    await custom_registry.refresh()
    return FactorCandidateRecord(**row)
