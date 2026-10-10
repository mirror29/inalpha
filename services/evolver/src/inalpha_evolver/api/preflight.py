"""Authenticated preparation checks without creating a run or redeeming model credentials."""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header
from inalpha_paper.account_id import account_id_from_user
from inalpha_paper.data_client import DataClient
from inalpha_paper.event_conversion import market_event_from_fact
from inalpha_shared.auth import User, get_current_user
from inalpha_shared.db import get_conn
from pydantic import BaseModel, ConfigDict

from ..config import get_evolver_settings
from ..data import FrozenBarsLoader
from ..data.event_coverage import inspect_event_coverage
from ..data.manifest import DatasetManifest
from ..event_client import fetch_event_snapshot
from ..governor.seed_resolver import resolve_seed
from .campaign_routes import _require_event_evolution, _validate_event_snapshot
from .loop_start import StartEvolutionLoopRequest, _validate_target_seed, validate_target_seed
from .request_hash import approval_request_digest
from .retry import retry_parent, retry_seed
from .schemas import EvolutionConfig, EvolutionPreparation, StartRunRequest


class RunPreflightResponse(BaseModel):
    """Public evidence of preparation; this response is not execution authorization."""

    seed_strategy_id: str
    seed_source_hash: str
    request_digest: str
    dataset_manifest: DatasetManifest
    estimated_max_cost_usd: float


router = APIRouter()


@router.post("/runs/preflight", response_model=RunPreflightResponse)
async def preflight_run(
    body: StartRunRequest,
    user: Annotated[User, Depends(get_current_user)],
    authorization: Annotated[str, Header()],
) -> RunPreflightResponse:
    """Check owned source and full market window before requesting paid execution."""
    owner = account_id_from_user(user)
    # Release the database connection before potentially slow connector preparation.
    async with get_conn() as conn:
        if body.retry_of_run_id is None:
            seed = await resolve_seed(conn, body.seed_strategy_id, owner)
        else:
            parent = await retry_parent(conn, body.retry_of_run_id, owner)
            seed = retry_seed(body, parent)
    settings = get_evolver_settings()
    async with DataClient(settings.data_service_url, authorization.split(" ", 1)[1]) as client:
        dataset = await FrozenBarsLoader(client).load(
            venue=body.config.venue,
            symbol=body.config.symbol,
            timeframe=body.config.timeframe,
            from_ts=body.config.from_ts,
            as_of=body.config.as_of,
        )
    prepared = body.model_copy(
        update={
            "preparation": EvolutionPreparation(
                seed_source_hash=seed.source_hash,
                dataset_content_sha256=dataset.manifest.content_sha256,
            )
        }
    )
    return RunPreflightResponse(
        seed_strategy_id=seed.reference,
        seed_source_hash=seed.source_hash,
        request_digest=approval_request_digest(prepared),
        dataset_manifest=dataset.manifest,
        estimated_max_cost_usd=body.budget * body.llm.pricing.estimated_max_usd_per_candidate,
    )


@router.post("/evolution-loops/preflight")
async def preflight_loop(
    body: StartEvolutionLoopRequest,
    user: Annotated[User, Depends(get_current_user)],
    authorization: Annotated[str, Header()],
) -> dict[str, object]:
    """Inspect real selection inputs before signing a paid loop launch; never create a run."""
    _require_event_evolution()
    owner = account_id_from_user(user)
    async with get_conn() as conn:
        await _validate_target_seed(conn, owner, body)
        await resolve_seed(conn, body.baseline.seed_strategy_id, owner)
    settings = get_evolver_settings()
    snapshot = await fetch_event_snapshot(
        body.campaign.event_snapshot_id,
        owner_account_id=owner,
        settings=settings,
    )
    _validate_event_snapshot(snapshot, body.campaign)
    async with DataClient(settings.data_service_url, authorization.split(" ", 1)[1]) as client:
        dataset = await FrozenBarsLoader(client).load(
            **{
                key: getattr(body.baseline.config, key)
                for key in ("venue", "symbol", "timeframe", "from_ts", "as_of")
            }
        )
    return {
        **inspect_event_coverage(
            dataset,
            [market_event_from_fact(fact) for fact in snapshot["facts"]],
            body.campaign.config.event_asset_code,
        ),
        "event_snapshot_id": str(body.campaign.event_snapshot_id),
        "events_sha256": snapshot["events_sha256"],
        "model_calls": 0,
        "execution_authorized": False,
    }


class ExperimentPreparationRequest(BaseModel):
    """Model-free preparation; contains neither model configuration nor spending authority."""

    model_config = ConfigDict(extra="forbid")
    target_kind: Literal["strategy_candidate", "paper_runner", "backtest_run", "e1_candidate"]
    target_id: UUID
    seed_strategy_id: str
    event_snapshot_id: UUID
    event_asset_code: str
    config: EvolutionConfig


@router.post("/evolution-loops/prepare")
async def prepare_experiment(
    body: ExperimentPreparationRequest,
    user: Annotated[User, Depends(get_current_user)],
    authorization: Annotated[str, Header()],
) -> dict[str, object]:
    """Prepare a new explicit window without creating a run or accessing model credentials."""
    _require_event_evolution()
    owner = account_id_from_user(user)
    async with get_conn() as conn:
        await validate_target_seed(conn, owner, body.target_kind, str(body.target_id), body.seed_strategy_id)
        seed = await resolve_seed(conn, body.seed_strategy_id, owner)
    settings = get_evolver_settings()
    snapshot = await fetch_event_snapshot(body.event_snapshot_id, owner_account_id=owner, settings=settings)
    if datetime.fromisoformat(snapshot["cutoff"].replace("Z", "+00:00")) != body.config.as_of:
        from inalpha_shared.errors import ConflictError
        raise ConflictError("snapshot cutoff differs from experiment", code="EVENT_SNAPSHOT_CUTOFF_MISMATCH")
    async with DataClient(settings.data_service_url, authorization.split(" ", 1)[1]) as client:
        dataset = await FrozenBarsLoader(client).load(
            **{key: getattr(body.config, key) for key in ("venue", "symbol", "timeframe", "from_ts", "as_of")}
        )
    return {
        **inspect_event_coverage(dataset, [market_event_from_fact(fact) for fact in snapshot["facts"]], body.event_asset_code),
        "seed_source_hash": seed.source_hash,
        "dataset_manifest": dataset.manifest.model_dump(mode="json"),
        "event_snapshot_id": str(body.event_snapshot_id),
        "events_sha256": snapshot["events_sha256"],
        "model_calls": 0,
        "execution_authorized": False,
    }
