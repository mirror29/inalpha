"""Evolver run 创建与列表端点。"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, Header, Request, status
from inalpha_paper.account_id import account_id_from_user
from inalpha_shared.auth import User, get_current_user
from inalpha_shared.db import DBConn
from inalpha_shared.errors import ConflictError, RateLimitedError

from ..config import get_evolver_settings
from ..governor.seed_resolver import resolve_seed
from ..storage import run_queries, runs
from .approval import verify_evolution_approval
from .cursor import decode_cursor, encode_cursor
from .presenters import run_response
from .request_hash import approval_request_digest, normalized_request
from .schemas import RunListResponse, RunStatusResponse, StartRunRequest

router = APIRouter()


@router.post("/runs", response_model=RunStatusResponse, status_code=status.HTTP_202_ACCEPTED)
async def start_run(
    body: StartRunRequest,
    request: Request,
    background: BackgroundTasks,
    db: DBConn,
    user: Annotated[User, Depends(get_current_user)],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=128)],
    evolution_credential: Annotated[
        str,
        Header(alias="X-Evolution-Credential", min_length=100, max_length=4096),
    ],
) -> RunStatusResponse:
    owner = account_id_from_user(user)
    settings = get_evolver_settings()
    config, request_hash = normalized_request(body)
    verify_evolution_approval(
        evolution_credential,
        owner_sub=user.user_id,
        operation_id=idempotency_key,
        config_id=body.llm.config_id,
        provider=body.llm.provider,
        llm_config_digest=body.llm.config_digest,
        request_digest=approval_request_digest(body),
        grant_purpose="e1_run",
        settings=settings,
    )
    async with db.transaction():
        await db.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (str(owner),))
        active = await run_queries.count_active(db, owner)
        seed = await resolve_seed(db, body.seed_strategy_id, owner)
        row, created = await runs.insert_run(
            db,
            owner_account_id=owner,
            requested_by_sub=user.user_id,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            seed_strategy_id=seed.reference,
            seed_source=seed.source_code,
            seed_hash=seed.source_hash,
            budget=body.budget,
            config=config,
            llm_snapshot=body.llm.model_dump(mode="json"),
            llm_credential_grant=evolution_credential,
            queued_at=datetime.now(UTC),
        )
        if not created and row["request_hash"] != request_hash:
            raise ConflictError("idempotency key reused", code="IDEMPOTENCY_KEY_REUSED")
        if created and active >= settings.evolver_account_active_limit:
            raise RateLimitedError("too many active evolution runs", code="EVOLUTION_RUN_LIMIT")
    if created:
        background.add_task(request.app.state.evolution_manager.notify_async)
    return run_response(row)


@router.get("/runs", response_model=RunListResponse)
async def list_runs(
    db: DBConn,
    user: Annotated[User, Depends(get_current_user)],
    limit: int = 20,
    cursor: str | None = None,
) -> RunListResponse:
    page_limit = min(max(limit, 1), 50)
    rows = await run_queries.list_runs(
        db,
        account_id_from_user(user),
        limit=page_limit + 1,
        cursor=decode_cursor(cursor),
    )
    has_more = len(rows) > page_limit
    items = rows[:page_limit]
    next_cursor = (
        encode_cursor(items[-1]["queued_at"], items[-1]["run_id"]) if has_more and items else None
    )
    return RunListResponse(
        items=[run_response(row, summary=row) for row in items],
        next_cursor=next_cursor,
    )
