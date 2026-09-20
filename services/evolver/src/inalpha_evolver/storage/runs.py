from __future__ import annotations

import json
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from psycopg import AsyncConnection

from ..loop_fencing import fenced_baseline_write

_COLUMNS = """run_id,owner_account_id,requested_by_sub,seed_strategy_id,budget,config,
llm_snapshot,llm_config_digest,llm_credential_grant,status,llm_cost_usd,queued_at,started_at,updated_at,finished_at,
venue,symbol,request_timeframe,data_timeframe,engine_timeframe,requested_as_of,
seed_source_snapshot,seed_source_hash,seed_report_snapshot,baseline_snapshot,dataset_manifest,
active_stage,failure_code,failure_message"""


async def insert_run(
    conn: AsyncConnection,
    *,
    owner_account_id: UUID,
    requested_by_sub: str,
    idempotency_key: str,
    request_hash: str,
    seed_strategy_id: str,
    seed_source: str,
    seed_hash: str,
    budget: int,
    config: dict[str, Any],
    llm_snapshot: dict[str, Any],
    llm_credential_grant: str,
    queued_at: datetime,
) -> tuple[dict[str, Any], bool]:
    run_id = uuid4()
    async with conn.cursor() as cur:
        await cur.execute(
            f"""INSERT INTO strategy_evo_runs(run_id,owner_account_id,requested_by_sub,
seed_strategy_id,budget,config,llm_snapshot,llm_config_digest,llm_credential_grant,status,idempotency_key,
request_hash,queued_at,llm_snapshot_required,llm_credential_grant_required,
venue,symbol,request_timeframe,data_timeframe,engine_timeframe,
requested_as_of,seed_source_snapshot,seed_source_hash) VALUES
(%s,%s,%s,%s,%s,%s,%s,%s,%s,'queued',%s,%s,%s,TRUE,TRUE,%s,%s,%s,%s,%s,%s,%s,%s)
ON CONFLICT(owner_account_id,idempotency_key) DO NOTHING RETURNING {_COLUMNS},request_hash""",
            (
                run_id,
                owner_account_id,
                requested_by_sub,
                seed_strategy_id,
                budget,
                json.dumps(config, default=str),
                json.dumps(llm_snapshot),
                llm_snapshot["config_digest"],
                llm_credential_grant,
                idempotency_key,
                request_hash,
                queued_at,
                config["venue"],
                config["symbol"],
                config["timeframe"],
                config["timeframe"],
                config["timeframe"],
                config["as_of"],
                seed_source,
                seed_hash,
            ),
        )
        row = await cur.fetchone()
        if row is not None:
            return dict(row), True
        await cur.execute(
            f"SELECT {_COLUMNS},request_hash FROM strategy_evo_runs WHERE owner_account_id=%s AND idempotency_key=%s",
            (owner_account_id, idempotency_key),
        )
        existing = await cur.fetchone()
    if existing is None:
        raise RuntimeError("idempotent run disappeared")
    return dict(existing), False


async def get_run(
    conn: AsyncConnection,
    run_id: UUID,
    owner_account_id: UUID | None = None,
) -> dict[str, Any] | None:
    sql = f"SELECT {_COLUMNS} FROM strategy_evo_runs WHERE run_id=%s"
    params: list[Any] = [run_id]
    if owner_account_id is not None:
        sql += " AND owner_account_id=%s"
        params.append(owner_account_id)
    async with conn.cursor() as cur:
        await cur.execute(sql, params)
        row = await cur.fetchone()
    return dict(row) if row else None


@fenced_baseline_write
async def clear_credential_grant(conn: AsyncConnection, run_id: UUID) -> None:
    """凭据 capability 兑换成功后立即从持久化队列清除。"""
    result = await conn.execute(
        """UPDATE strategy_evo_runs
        SET llm_credential_grant=NULL,llm_credential_grant_required=FALSE
        WHERE run_id=%s AND status='running'""",
        (run_id,),
    )
    if result.rowcount != 1:
        raise RuntimeError("credential grant can only be cleared for a running run")


@fenced_baseline_write
async def transition(
    conn: AsyncConnection,
    run_id: UUID,
    *,
    from_statuses: tuple[str, ...],
    to_status: str,
    values: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    updates: dict[str, Any] = {"status": to_status, "updated_at": datetime.now().astimezone()}
    updates.update(values or {})
    assignments = ",".join(f"{key}=%s" for key in updates)
    params: list[Any] = [
        json.dumps(value) if isinstance(value, dict) else value for value in updates.values()
    ]
    params.extend([run_id, list(from_statuses)])
    async with conn.cursor() as cur:
        await cur.execute(
            f"UPDATE strategy_evo_runs SET {assignments} WHERE run_id=%s AND status=ANY(%s) RETURNING {_COLUMNS}",
            params,
        )
        row = await cur.fetchone()
    return dict(row) if row else None
