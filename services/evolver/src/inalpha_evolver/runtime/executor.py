"""queued run 的数据加载、评估器构建与执行。"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any
from uuid import UUID

import jwt
from inalpha_paper.data_client import DataClient
from inalpha_paper.evaluation_executor import KillableEngineRunner
from inalpha_paper.evolution_execution_policy import frozen_protection_kwargs
from inalpha_shared.db import get_conn

from ..config import EvolverSettings
from ..data import FrozenBarsLoader, FrozenDataset
from ..evaluator import FrozenDatasetEvaluator
from ..loop_llm import LoopModelScope
from ..owner_llm import build_owner_mutator
from ..storage import runs
from .generation import execute_generation


async def execute_run(
    run: dict[str, Any],
    *,
    mutator: Any | None,
    settings: EvolverSettings,
) -> None:
    config = _parse_config(run["config"])
    run = {**run, "config": config}
    token = _service_token(run["owner_account_id"], settings)
    async with DataClient(settings.data_service_url, token) as client:
        dataset = await FrozenBarsLoader(client).load(
            venue=config["venue"],
            symbol=config["symbol"],
            timeframe=config["timeframe"],
            from_ts=config["from_ts"],
            as_of=config["as_of"],
        )
    await execute_frozen_run(run, dataset=dataset, mutator=mutator, settings=settings)


async def execute_frozen_run(
    run: dict[str, Any],
    *,
    dataset: FrozenDataset,
    mutator: Any | None,
    settings: EvolverSettings,
    loop_scope: LoopModelScope | None = None,
) -> None:
    """Evaluate an already frozen dataset without another market-data request."""
    config = _parse_config(run["config"])
    async with get_conn() as conn:
        current = await runs.transition(
            conn,
            run["run_id"],
            from_statuses=("running",),
            to_status="running",
            values={
                "data_timeframe": dataset.manifest.data_timeframe,
                "engine_timeframe": dataset.manifest.canonical_timeframe,
                "dataset_manifest": dataset.manifest.model_dump(mode="json"),
                "active_stage": "evaluating_seed",
            },
        )
    if current is None:
        raise asyncio.CancelledError
    evaluator = FrozenDatasetEvaluator(
        dataset=dataset,
        runner=KillableEngineRunner(
            timeout_s=settings.evolver_job_timeout_s,
            mem_gb=settings.evolver_job_mem_gb,
        ),
        initial_cash=float(config["initial_cash"]),
        fee_rate=float(config.get("fee_rate", 0.001)),
        validation_split=float(config.get("validation_split", 0.3)),
        trading_mode=config.get("trading_mode", "spot"),
        leverage=int(config.get("leverage", 1)),
        funding_rate=float(config.get("funding_rate", 0)),
        params=config.get("params", {}),
        **frozen_protection_kwargs(config),
    )
    async with _run_mutator(run, mutator, settings, loop_scope=loop_scope) as active_mutator:
        await execute_generation(run, mutator=active_mutator, evaluator=evaluator)


@asynccontextmanager
async def _run_mutator(
    run: dict[str, Any],
    injected: Any | None,
    settings: EvolverSettings,
    *,
    loop_scope: LoopModelScope | None = None,
) -> AsyncIterator[Any]:
    """生产按 run 解析 owner 凭据；测试注入路径不接管其生命周期。"""
    if injected is not None:
        yield injected
        return
    owner_mutator = (
        await build_owner_mutator(run, settings, loop_scope=loop_scope)
        if loop_scope is not None else await build_owner_mutator(run, settings)
    )
    async with get_conn() as conn:
        await runs.clear_credential_grant(conn, run["run_id"])
    try:
        yield owner_mutator
    finally:
        await owner_mutator.close()


def _parse_config(config: dict[str, Any]) -> dict[str, Any]:
    """把 JSONB 中的 ISO 时间恢复为 loader 所需的 datetime。"""
    parsed = dict(config)
    for key in ("from_ts", "as_of"):
        value = parsed.get(key)
        if isinstance(value, str):
            parsed[key] = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed


def _service_token(account_id: UUID, settings: EvolverSettings) -> str:
    return jwt.encode(
        {"sub": str(account_id), "exp": int(time.time()) + settings.service_token_ttl_s},
        settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
    )
