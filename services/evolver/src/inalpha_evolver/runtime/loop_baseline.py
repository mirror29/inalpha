"""Resume a loop's baseline using only discovery from its immutable full dataset."""

from datetime import UTC, datetime
from typing import Any

from inalpha_paper.data_client import DataClient
from inalpha_paper.event_conversion import market_event_from_fact
from inalpha_shared.db import get_conn
from inalpha_shared.errors import ValidationError

from ..api.campaign_routes import _validate_event_snapshot
from ..api.schemas import CreateCampaignRequest
from ..config import EvolverSettings
from ..data import FrozenBarsLoader
from ..data.event_coverage import inspect_event_coverage
from ..data.persistent_snapshot import (
    decode_frozen_dataset,
    discovery_dataset,
    get_loop_data_snapshot,
    persist_loop_data_snapshot,
)
from ..event_client import fetch_event_snapshot
from ..loop_fencing import BaselineLease, BaselineLeaseLost, baseline_lease
from ..loop_llm import LoopModelScope
from ..storage import loop_dispatch, runs
from .executor import _parse_config, _service_token, execute_frozen_run, validate_prepared_dataset


async def execute_loop_baseline(
    loop: dict[str, Any],
    settings: EvolverSettings,
    *,
    mutator: Any | None = None,
) -> None:
    """A restart reuses bars and completed slots; only a current lease can finish the step."""
    lease = BaselineLease(
        loop["loop_id"],
        loop["owner_account_id"],
        loop["e1_run_id"],
        loop["lease_token"],
    )
    scope = {
        "loop_id": lease.loop_id,
        "owner_account_id": lease.owner_account_id,
        "lease_token": lease.lease_token,
    }
    with baseline_lease(lease):
        async with get_conn() as conn:
            run = await runs.get_run(conn, lease.run_id, lease.owner_account_id)
            persisted = await get_loop_data_snapshot(conn, lease.loop_id, lease.owner_account_id)
        if run is None or run["status"] not in {"queued", "running", "completed"}:
            raise RuntimeError("loop baseline is missing or no longer executable")
        if run["status"] == "completed" and persisted is None:
            raise RuntimeError("completed automatic baseline is missing its frozen dataset")
        if run["status"] != "completed":
            async with get_conn() as conn:
                run = await runs.transition(
                    conn,
                    lease.run_id,
                    from_statuses=("queued", "running"),
                    to_status="running",
                    values={
                        "started_at": run.get("started_at") or datetime.now(UTC),
                        "failure_code": None,
                        "failure_message": None,
                    },
                )
            if run is None:
                raise BaselineLeaseLost("baseline is no longer running")
            if persisted is None:
                config = _parse_config(run["config"])
                async with DataClient(
                    settings.data_service_url,
                    _service_token(lease.owner_account_id, settings),
                    timeout=settings.evolver_data_timeout_s,
                ) as client:
                    dataset = await FrozenBarsLoader(client).load(
                        **{
                            key: config[key]
                            for key in ("venue", "symbol", "timeframe", "from_ts", "as_of")
                        }
                    )
                async with get_conn() as conn:
                    persisted = await persist_loop_data_snapshot(conn, **scope, dataset=dataset)
            if run.get("config", {}).get("preparation") is not None:
                validate_prepared_dataset(run, decode_frozen_dataset(persisted))
            await require_loop_event_coverage(loop, persisted, settings)
            await execute_frozen_run(
                run,
                dataset=discovery_dataset(persisted),
                mutator=mutator,
                settings=settings,
                loop_scope=LoopModelScope(**scope, phase="baseline"),
            )
        async with get_conn() as conn:
            if not await loop_dispatch.complete_step(
                conn,
                **scope,
                step_key="baseline",
                output_id=lease.run_id,
            ):
                raise BaselineLeaseLost("baseline completion lost its worker lease")


async def require_loop_event_coverage(
    loop: dict[str, Any],
    persisted: dict[str, Any],
    settings: EvolverSettings,
) -> None:
    """Recheck actual immutable inputs before any baseline model credential is redeemed."""
    body = CreateCampaignRequest.model_validate(loop["frozen_config"]["campaign_request"])
    snapshot = await fetch_event_snapshot(
        body.event_snapshot_id,
        owner_account_id=loop["owner_account_id"],
        settings=settings,
    )
    _validate_event_snapshot(snapshot, body)
    coverage = inspect_event_coverage(
        decode_frozen_dataset(persisted),
        [market_event_from_fact(fact) for fact in snapshot["facts"]],
        body.config.event_asset_code,
    )
    if coverage["status"] == "blocked":
        raise ValidationError(
            "selection event coverage cannot meet the minimum matched-pair requirement",
            details=coverage,
            code="E2_INPUT_COVERAGE_INSUFFICIENT",
        )
