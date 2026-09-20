"""owner-scoped 种子解析与冻结。"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from uuid import UUID

from inalpha_paper.storage.strategy_candidates import get_owned_candidate
from inalpha_paper.strategy_preparation import prepare_strategy_source
from inalpha_shared.errors import NotFoundError
from psycopg import AsyncConnection

from ..storage import candidates as evolution_candidates
from .seed import SEED_STRATEGY_CODE


@dataclass(frozen=True, slots=True)
class ResolvedSeed:
    reference: str
    source_code: str
    source_hash: str


async def resolve_seed(
    conn: AsyncConnection,
    reference: str,
    owner_account_id: UUID,
) -> ResolvedSeed:
    if reference == "sma_cross_v1":
        source = SEED_STRATEGY_CODE
    elif reference.startswith("candidate:"):
        try:
            candidate_id = UUID(reference.removeprefix("candidate:"))
        except ValueError as exc:
            raise _not_found(reference) from exc
        row = await get_owned_candidate(conn, candidate_id, owner_account_id)
        # Promotion is a trading-lifecycle gate, not a research gate. Owner drafts may be
        # evolved after the same source audit; rejected and foreign candidates stay hidden.
        if row is None or row.get("status") not in {"candidate", "promoted"}:
            raise _not_found(reference)
        source = row["code"]
    elif reference.startswith("evolution_candidate:"):
        try:
            candidate_id = UUID(reference.removeprefix("evolution_candidate:"))
        except ValueError as exc:
            raise _not_found(reference) from exc
        row = await evolution_candidates.get_candidate(conn, candidate_id, owner_account_id)
        if (
            row is None
            or row.get("outcome") != "succeeded"
            or not isinstance(row.get("source_code"), str)
        ):
            raise _not_found(reference)
        source = row["source_code"]
    else:
        raise _not_found(reference)
    prepare_strategy_source(source)
    return ResolvedSeed(
        reference=reference,
        source_code=source,
        source_hash=hashlib.sha256(source.encode()).hexdigest(),
    )


def _not_found(reference: str) -> NotFoundError:
    return NotFoundError(
        f"evolution seed {reference!r} not found",
        code="EVOLUTION_SEED_NOT_FOUND",
    )


__all__ = ["ResolvedSeed", "resolve_seed"]
