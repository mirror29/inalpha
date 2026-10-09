"""Resolve explicit retries without changing the original research question or frozen seed."""
from hashlib import sha256
from typing import Any
from uuid import UUID

from inalpha_paper.strategy_preparation import prepare_strategy_source
from inalpha_shared.errors import ConflictError, NotFoundError
from psycopg import AsyncConnection

from ..governor.seed_resolver import ResolvedSeed
from ..storage import runs
from .schemas import EvolutionConfig, StartRunRequest


async def retry_parent(conn: AsyncConnection, run_id: UUID, owner: UUID) -> dict[str, Any]:
    row = await runs.get_run(conn, run_id, owner)
    if row is None:
        raise NotFoundError("run not found", code="EVOLUTION_RUN_NOT_FOUND")
    if not isinstance(row.get("seed_source_snapshot"), str) or not isinstance(row.get("seed_source_hash"), str):
        raise ConflictError("legacy run has no frozen seed snapshot to retry", code="EVOLUTION_RETRY_SNAPSHOT_MISSING")
    if row["status"] not in {"failed", "aborted"}:
        raise ConflictError("only failed or aborted attempts can be retried", code="EVOLUTION_RETRY_NOT_ALLOWED")
    async with conn.cursor() as cur:
        await cur.execute("SELECT 1 FROM evolution_loops WHERE e1_run_id=%s", (run_id,))
        if await cur.fetchone() is not None:
            raise ConflictError("automatic workflow baselines must use workflow recovery", code="EVOLUTION_RETRY_NOT_ALLOWED")
    return row


def retry_seed(body: StartRunRequest, parent: dict[str, Any]) -> ResolvedSeed:
    """Use the audited original source, even if the live source candidate later changed."""
    if not isinstance(parent.get("seed_source_snapshot"), str) or not isinstance(parent.get("seed_source_hash"), str):
        raise ConflictError("original seed snapshot is missing", code="EVOLUTION_RETRY_SNAPSHOT_MISSING")
    original = EvolutionConfig.model_validate(parent["config"])
    if (
        body.config.model_dump(mode="json") != original.model_dump(mode="json")
        or body.seed_strategy_id != parent["seed_strategy_id"]
        or body.budget != parent["budget"]
    ):
        raise ConflictError("retry must preserve the original seed, window, parameters and candidate count", code="EVOLUTION_RETRY_SCOPE_CHANGED")
    source = parent["seed_source_snapshot"]
    source_hash = sha256(source.encode()).hexdigest()
    if source_hash != parent["seed_source_hash"]:
        raise ConflictError("original source snapshot is inconsistent", code="EVOLUTION_RETRY_SOURCE_INVALID")
    prepare_strategy_source(source)
    return ResolvedSeed(reference=parent["seed_strategy_id"], source_code=source, source_hash=source_hash)
