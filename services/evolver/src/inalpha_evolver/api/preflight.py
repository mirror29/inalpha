"""Authenticated preparation checks without creating a run or redeeming model credentials."""
from typing import Annotated

from fastapi import APIRouter, Depends, Header
from inalpha_paper.account_id import account_id_from_user
from inalpha_paper.data_client import DataClient
from inalpha_shared.auth import User, get_current_user
from inalpha_shared.db import get_conn
from pydantic import BaseModel

from ..config import get_evolver_settings
from ..data import FrozenBarsLoader
from ..data.manifest import DatasetManifest
from ..governor.seed_resolver import resolve_seed
from .request_hash import approval_request_digest
from .retry import retry_parent, retry_seed
from .schemas import EvolutionPreparation, StartRunRequest


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
    prepared = body.model_copy(update={"preparation": EvolutionPreparation(
        seed_source_hash=seed.source_hash,
        dataset_content_sha256=dataset.manifest.content_sha256,
    )})
    return RunPreflightResponse(
        seed_strategy_id=seed.reference,
        seed_source_hash=seed.source_hash,
        request_digest=approval_request_digest(prepared),
        dataset_manifest=dataset.manifest,
        estimated_max_cost_usd=body.budget * body.llm.pricing.estimated_max_usd_per_candidate,
    )
