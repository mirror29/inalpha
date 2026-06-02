"""``/strategy_runs`` —— live runner 的 start / stop / list（D-11 issue #1）。

- ``POST /strategy_runs``：给一个 **promoted** candidate 起后台 live 跑
- ``POST /strategy_runs/{id}/stop``：停一个 run
- ``GET /strategy_runs``：列当前账户的 run（状态 / 累计 pnl / error_log）

护栏：candidate 必须 ``status='promoted'``（否则 422）；同 candidate 同时只能一个
running（DB 部分唯一索引，撞 → 409 ``STRATEGY_RUN_ALREADY_RUNNING``）。下单走 plan/exec
机器自动审批，正当性靠"人先 promote + 人显式 start"两道（见 ``live_runner`` 模块注释）。
"""
from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query, Request
from inalpha_shared.auth import User, get_current_user
from inalpha_shared.db import DBConn
from inalpha_shared.errors import InalphaError, NotFoundError

from ..account_id import account_id_from_user
from ..schemas import StartStrategyRunRequest, StrategyRunRecord
from ..storage import strategy_candidates as candidates_store
from ..storage import strategy_runs as runs_store

router = APIRouter(tags=["strategy_runs"])


class CandidateNotPromotedError(InalphaError):
    code = "CANDIDATE_NOT_PROMOTED"
    status_code = 422


@router.post("/strategy_runs", response_model=StrategyRunRecord)
async def start_strategy_run(
    req: StartStrategyRunRequest,
    request: Request,
    db: DBConn,
    user: Annotated[User, Depends(get_current_user)],
) -> StrategyRunRecord:
    """给一个 promoted candidate 起 live run（后台按 timeframe 自动跑）。"""
    account_id = account_id_from_user(user)

    candidate = await candidates_store.get_candidate(db, req.candidate_id)
    if candidate is None:
        raise NotFoundError(
            f"candidate {req.candidate_id} not found",
            details={"candidate_id": str(req.candidate_id)},
        )
    if candidate["status"] != "promoted":
        raise CandidateNotPromotedError(
            f"candidate {req.candidate_id} is '{candidate['status']}', must be 'promoted' "
            "before it can run live",
            details={"candidate_id": str(req.candidate_id), "status": candidate["status"]},
        )

    # 撞同 candidate 已 running → runs_store.insert 抛 StrategyRunConflict(409)
    run = await runs_store.insert(
        db,
        candidate_id=req.candidate_id,
        account_id=account_id,
        venue=req.venue,
        symbol=req.symbol,
        timeframe=req.timeframe,
        params=req.params,
    )

    manager = request.app.state.live_runner_manager
    manager.start(run)
    return _row_to_record(run)


@router.post("/strategy_runs/{run_id}/stop", response_model=StrategyRunRecord)
async def stop_strategy_run(
    request: Request,
    db: DBConn,
    user: Annotated[User, Depends(get_current_user)],
    run_id: Annotated[UUID, Path()],
) -> StrategyRunRecord:
    """停一个 run（仅限本账户）。"""
    account_id = account_id_from_user(user)
    run = await runs_store.get(db, run_id)
    if run is None or run["account_id"] != account_id:
        raise NotFoundError(
            f"strategy_run {run_id} not found", details={"run_id": str(run_id)}
        )

    manager = request.app.state.live_runner_manager
    await manager.stop(run_id)
    updated = await runs_store.get(db, run_id)
    return _row_to_record(updated or run)


@router.get("/strategy_runs", response_model=list[StrategyRunRecord])
async def list_strategy_runs(
    db: DBConn,
    user: Annotated[User, Depends(get_current_user)],
    status: Annotated[str | None, Query()] = None,
) -> list[StrategyRunRecord]:
    """列出当前账户的 live run。"""
    account_id = account_id_from_user(user)
    rows = await runs_store.list_by_account(db, account_id, status=status)
    return [_row_to_record(r) for r in rows]


def _row_to_record(row: dict[str, Any]) -> StrategyRunRecord:
    return StrategyRunRecord(
        id=row["id"],
        candidate_id=row["candidate_id"],
        account_id=row["account_id"],
        status=row["status"],
        venue=row["venue"],
        symbol=row["symbol"],
        timeframe=row["timeframe"],
        params=row.get("params") or {},
        last_bar_ts=row.get("last_bar_ts"),
        cumulative_pnl=float(row["cumulative_pnl"]),
        error_log=row.get("error_log") or [],
        started_at=row["started_at"],
        stopped_at=row.get("stopped_at"),
    )
