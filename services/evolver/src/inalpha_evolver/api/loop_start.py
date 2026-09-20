"""One signed, budgeted launch transaction for the complete research workflow."""

import hashlib
import json
import struct
from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Request
from inalpha_paper.account_id import account_id_from_user
from inalpha_shared.auth import User, get_current_user
from inalpha_shared.db import DBConn
from inalpha_shared.errors import ConflictError, RateLimitedError
from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..config import get_evolver_settings
from ..event_client import fetch_event_snapshot
from ..forward_client import fetch_execution_policy
from ..governor.seed_resolver import resolve_seed
from ..storage import loop_authorizations, loops, run_queries, runs
from .approval import verify_evolution_approval
from .campaign_routes import _require_event_evolution, _validate_event_snapshot
from .request_hash import approval_request_digest, normalized_request
from .schemas import (
    CreateCampaignRequest,
    EvolutionLoopResponse,
    StartRunRequest,
    campaign_request_digest,
)


class StartEvolutionLoopRequest(BaseModel):
    """Freeze the complete launch intent without exposing secrets or authorizing trading."""

    model_config = ConfigDict(extra="forbid")
    baseline: StartRunRequest
    campaign: CreateCampaignRequest
    max_cost_usd: float = Field(gt=0, le=100, allow_inf_nan=False)

    @model_validator(mode="after")
    def validate_scope(self):
        if self.campaign.source_run_id is not None or self.campaign.hypotheses:
            raise ValueError("loop startup owns its baseline and generation-one hypotheses")
        if self.campaign.target_kind not in {
            "strategy_candidate",
            "paper_runner",
            "backtest_run",
            "e1_candidate",
        }:
            raise ValueError("loop startup requires a strategy or simulation target")
        if not self.campaign.target_id:
            raise ValueError("loop startup requires a stable target id")
        UUID(self.campaign.target_id)
        if self.baseline.llm != self.campaign.llm:
            raise ValueError("loop stages must use the same frozen model snapshot")
        for key in (
            "venue",
            "symbol",
            "timeframe",
            "from_ts",
            "as_of",
            "initial_cash",
            "fee_rate",
            "funding_rate",
            "trading_mode",
            "leverage",
        ):
            if getattr(self.baseline.config, key) != getattr(self.campaign.config, key):
                raise ValueError(f"loop stage configuration mismatch: {key}")
        required = (
            self.baseline.budget + 10
        ) * self.baseline.llm.pricing.estimated_max_usd_per_candidate
        if self.max_cost_usd + 1e-12 < required:
            raise ValueError("loop budget cannot cover the planned model calls")
        return self


def loop_request_digest(body: StartEvolutionLoopRequest) -> str:
    """Bind both stage requests and the hard budget in a cross-language digest."""
    canonical = [
        "evolution-loop-v1",
        approval_request_digest(body.baseline),
        campaign_request_digest(body.campaign),
        struct.pack(">d", body.max_cost_usd).hex(),
    ]
    return hashlib.sha256(json.dumps(canonical, separators=(",", ":")).encode()).hexdigest()


router = APIRouter(prefix="/evolution-loops")


@router.post("/start", response_model=EvolutionLoopResponse, status_code=202)
async def start_evolution_loop(
    body: StartEvolutionLoopRequest,
    request: Request,
    background: BackgroundTasks,
    db: DBConn,
    user: Annotated[User, Depends(get_current_user)],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=128)],
    evolution_credential: Annotated[
        str, Header(alias="X-Evolution-Credential", min_length=100, max_length=4096)
    ],
) -> EvolutionLoopResponse:
    """Create E1, durable loop and authorization atomically, then wake its dispatcher."""
    _require_event_evolution()
    manager = getattr(request.app.state, "loop_manager", None)
    if manager is None or not manager.healthy:
        raise HTTPException(status_code=503, detail="durable evolution loop dispatcher unavailable")
    owner = account_id_from_user(user)
    settings = get_evolver_settings()
    digest = loop_request_digest(body)
    llm = body.baseline.llm
    verify_evolution_approval(
        evolution_credential,
        owner_sub=user.user_id,
        operation_id=idempotency_key,
        config_id=llm.config_id,
        provider=llm.provider,
        llm_config_digest=llm.config_digest,
        request_digest=digest,
        grant_purpose="evolution_loop_start",
        settings=settings,
    )
    target_kind, target_id = body.campaign.target_kind, body.campaign.target_id
    assert target_kind is not None and target_id is not None
    async with db.transaction():
        await db.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (str(owner),))
        await db.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
            (f"{owner}:{target_kind}:{target_id}",),
        )
        async with db.cursor() as cur:
            await cur.execute(
                """SELECT l.loop_id,a.request_digest FROM evolution_loops l
JOIN evolution_loop_authorizations a USING(loop_id)
WHERE l.owner_account_id=%s AND l.operation_id=%s""",
                (owner, idempotency_key),
            )
            previous = await cur.fetchone()
        if previous:
            if previous["request_digest"] != digest:
                raise ConflictError("loop operation reused", code="IDEMPOTENCY_KEY_REUSED")
            row = await loops.get_loop(db, previous["loop_id"], owner)
            assert row is not None
            return EvolutionLoopResponse(**row)
        active = await loops.get_active_loop_for_target(db, owner, target_kind, target_id)
        if active is not None:
            return EvolutionLoopResponse(**active)
        await _validate_target_seed(db, owner, body)
        seed = await resolve_seed(db, body.baseline.seed_strategy_id, owner)
        if await run_queries.count_active(db, owner) >= settings.evolver_account_active_limit:
            raise RateLimitedError("too many active evolution runs", code="EVOLUTION_RUN_LIMIT")
        snapshot = await fetch_event_snapshot(
            body.campaign.event_snapshot_id, owner_account_id=owner, settings=settings
        )
        _validate_event_snapshot(snapshot, body.campaign)
        config, _ = normalized_request(body.baseline)
        config["protection_policy"] = await fetch_execution_policy(owner, settings)
        run, _ = await runs.insert_run(
            db,
            owner_account_id=owner,
            requested_by_sub=user.user_id,
            idempotency_key=idempotency_key,
            request_hash=digest,
            seed_strategy_id=seed.reference,
            seed_source=seed.source_code,
            seed_hash=seed.source_hash,
            budget=body.baseline.budget,
            config=config,
            llm_snapshot=llm.model_dump(mode="json"),
            llm_credential_grant=evolution_credential,
            queued_at=datetime.now(UTC),
        )
        if run["request_hash"] != digest:
            raise ConflictError("loop operation reused", code="IDEMPOTENCY_KEY_REUSED")
        row = await loops.ensure_for_e1_run(
            db,
            owner_account_id=owner,
            requested_by_sub=user.user_id,
            operation_id=idempotency_key,
            target_kind=target_kind,
            target_id=target_id,
            target_snapshot={"seed_reference": seed.reference},
            e1_run_id=run["run_id"],
            frozen_config={**config, "campaign_request": body.campaign.model_dump(mode="json")},
            budget={
                "e1_candidates": body.baseline.budget,
                "max_generations": 5,
                "max_cost_usd": body.max_cost_usd,
            },
        )
        await loop_authorizations.register(
            db,
            loop_id=row["loop_id"],
            owner_account_id=owner,
            request_digest=digest,
            max_cost_usd=Decimal(str(body.max_cost_usd)),
        )
    background.add_task(manager.notify_async)
    return EvolutionLoopResponse(**row)


async def _validate_target_seed(db, owner, body: StartEvolutionLoopRequest) -> None:
    """Do not let a signed target label point at a different strategy or another owner's run."""
    kind, target_id = body.campaign.target_kind, body.campaign.target_id
    expected = None
    if kind == "strategy_candidate":
        expected = f"candidate:{target_id}"
    elif kind == "e1_candidate":
        expected = f"evolution_candidate:{target_id}"
    else:
        async with db.cursor() as cur:
            if kind == "paper_runner":
                await cur.execute(
                    "SELECT candidate_id FROM strategy_runs WHERE id=%s AND account_id=%s",
                    (target_id, owner),
                )
                row = await cur.fetchone()
                expected = f"candidate:{row['candidate_id']}" if row else None
            else:
                await cur.execute(
                    "SELECT strategy_code,config FROM backtest_runs WHERE id=%s AND account_id=%s",
                    (target_id, str(owner)),
                )
                row = await cur.fetchone()
                if row:
                    candidate_id = row["config"].get("candidate_id")
                    expected = f"candidate:{candidate_id}" if candidate_id else row["strategy_code"]
                    if expected == "sma_cross":
                        expected = "sma_cross_v1"
    if not expected or expected != body.baseline.seed_strategy_id:
        raise ConflictError("evolution target does not match its seed", code="LOOP_TARGET_MISMATCH")
