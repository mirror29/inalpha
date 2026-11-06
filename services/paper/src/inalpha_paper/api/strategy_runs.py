"""``/strategy_runs`` —— live runner 的 start / stop / list（D-11 issue #1）。

- ``POST /strategy_runs``：给一个 **promoted** candidate 起后台 live 跑
- ``POST /strategy_runs/{id}/stop``：停一个 run
- ``GET /strategy_runs``：列当前账户的 run（状态 / 累计 pnl / error_log）

护栏：candidate 必须 ``status='promoted'``（否则 422）；同 candidate 同时只能一个
running（DB 部分唯一索引，撞 → 409 ``STRATEGY_RUN_ALREADY_RUNNING``）。下单走 plan/exec
机器自动审批，正当性靠"人先 promote + 人显式 start"两道（见 ``live_runner`` 模块注释）。
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, Header, Path, Query, Request
from inalpha_shared.auth import User, get_current_user
from inalpha_shared.db import DBConn
from inalpha_shared.errors import InalphaError, NotFoundError

from ..account_id import account_id_from_user
from ..config import PaperSettings, get_paper_settings
from ..data_client import DataClient
from ..execution import perp_margin
from ..execution.currency_resolver import resolve_currency
from ..fx import BaseCurrencyConverter
from ..schemas import (
    StartStrategyRunRequest,
    StrategyRunDecisionRecord,
    StrategyRunRecord,
)
from ..storage import accounts as accounts_store
from ..storage import positions as positions_store
from ..storage import run_wallets as wallets_store
from ..storage import strategy_candidates as candidates_store
from ..storage import strategy_runs as runs_store

_logger = logging.getLogger(__name__)

router = APIRouter(tags=["strategy_runs"])


class CandidateNotPromotedError(InalphaError):
    code = "CANDIDATE_NOT_PROMOTED"
    status_code = 422


class CandidateNotOwnedError(InalphaError):
    """调用者不是该 candidate 的所有者——不能挂别人的策略在自己账户跑（issue #36.1）。"""

    code = "CANDIDATE_NOT_OWNED"
    status_code = 403


class TooManyRunningRunsError(InalphaError):
    """单账户 running run 已达上限（资源软护栏 issue #36.2）。"""

    code = "TOO_MANY_RUNNING_RUNS"
    status_code = 429


class SymbolRunConflictError(InalphaError):
    """同账户同 (venue, symbol) 已有 running run(issue #108 短期守门)。

    positions 主键 = (account_id, venue, symbol) 一标的一行,两个 run 撞同标的会共享
    同一行持仓互相打架(PnL 归属错乱 / 一个 run 的出场平掉另一个的仓)。
    """

    code = "SYMBOL_RUN_CONFLICT"
    status_code = 409


class InsufficientCashForRunError(InalphaError):
    """账户折算可用现金 ≤ 0,无法为新 run 分配默认额度。"""

    code = "INSUFFICIENT_CASH_FOR_RUN"
    status_code = 422


@router.post("/strategy_runs", response_model=StrategyRunRecord)
async def start_strategy_run(
    req: StartStrategyRunRequest,
    request: Request,
    db: DBConn,
    background_tasks: BackgroundTasks,
    user: Annotated[User, Depends(get_current_user)],
    settings: Annotated[PaperSettings, Depends(get_paper_settings)],
    authorization: Annotated[str | None, Header()] = None,
) -> StrategyRunRecord:
    """给一个 promoted candidate 起 live run（后台按 timeframe 自动跑）。"""
    account_id = account_id_from_user(user)

    # perp 资格硬 gate(perp 须 crypto + USDT-M 永续标的 + 杠杆 1..20,否则 422)。spot 放行。
    perp_margin.validate_perp_eligibility(
        venue=req.venue,
        symbol=req.symbol,
        trading_mode=req.trading_mode,
        leverage=req.leverage,
    )

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

    # 归属校验（issue #36.1）：不能挂别人 promote 的 candidate 在自己账户跑。
    # owner_account_id 与 account_id 同源（account_id_from_user），非 UUID sub 也可比。
    # 遗留 NULL 行（pre-migration 老数据）无法追溯归属 → 放行（有界 fail-open），留 warning。
    owner_account_id = candidate.get("owner_account_id")
    if owner_account_id is None:
        _logger.warning(
            "candidate %s 无 owner_account_id（pre-migration 老数据），归属校验放行",
            req.candidate_id,
        )
    elif owner_account_id != account_id:
        raise CandidateNotOwnedError(
            f"candidate {req.candidate_id} is owned by another account; "
            "cannot start a live run for a candidate you do not own",
            details={"candidate_id": str(req.candidate_id)},
        )

    # Fetch conversion before acquiring account locks; stale/missing rates fail closed.
    acct = await accounts_store.get_or_create(db, account_id)
    quote = resolve_currency(req.venue, req.symbol)
    token = authorization.removeprefix("Bearer ").strip() if authorization else None
    async with DataClient(settings.data_service_url, token or "") as dc:
        converter = BaseCurrencyConverter(acct["base_currency"], dc)
        rate = await converter.rate(quote)
        for currency in acct["cash_balances"]:
            await converter.rate(currency)
    if rate is None or converter.warnings:
        raise wallets_store.WalletConflict("Fresh conversion rate required to allocate capital")
    async with db.transaction():
        acct = await accounts_store.get_or_create(db, account_id, for_update=True)
        if acct["base_currency"] != converter.base:
            raise wallets_store.WalletConflict("Account currency changed; retry allocation")
        running = await runs_store.count_running_by_account(db, account_id)
        if running >= settings.live_max_running_runs_per_account:
            raise TooManyRunningRunsError("Stop a running strategy before creating another")
        offline = converter.offline_copy()
        available = await wallets_store.available_cash(db, acct, offline)
        allocation = (
            Decimal(str(req.allocation))
            if req.allocation is not None
            else min(Decimal("10000"), available)
        )
        if allocation <= 0:
            raise InsufficientCashForRunError("No available main-account cash")
        run = await runs_store.insert(
            db,
            candidate_id=req.candidate_id,
            account_id=account_id,
            venue=req.venue,
            symbol=req.symbol,
            timeframe=req.timeframe,
            params=req.params,
            trading_mode=req.trading_mode,
            leverage=req.leverage,
            allocation=allocation,
        )
        await wallets_store.create(
            db,
            account_id,
            run["id"],
            allocation,
            quote_currency=quote,
            quote_rate=rate,
            converter=offline,
        )
        run["accounting_status"] = "verified"

    # 策略误投软告警(不硬拦):perp 模式下若策略疑似 long-only(有 _is_long、无做空标记),
    # 其出场 SELL 会被当开空,可能漂移(d4404933 同型)。只 warn 让用户知情,放行。
    if req.trading_mode == "perp":
        code = candidate.get("code") or ""
        looks_long_only = "_is_long" in code and "_is_short" not in code and "is_short" not in code
        if looks_long_only:
            await runs_store.append_log(
                db,
                run["id"],
                "warn",
                "perp 模式但策略疑似 long-only(无做空/cover 逻辑):出场 SELL 会被当开空、"
                "可能漂移成平不掉的空头。请确认该策略含做空入场/出场/cover 逻辑。",
            )

    # 后台 task 在**响应发出 + DBConn 事务提交后**才起（M-4）：避免在 insert 尚未
    # 提交时就拉起 loop——否则若 handler 在 start 后抛错回滚 run 行，task 会变成写无主
    # run_id 的孤儿（decisions 无 FK、update_progress 静默 0 行、reconcile 也看不到）。
    manager = request.app.state.live_runner_manager
    background_tasks.add_task(manager.start_async, run)
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
        raise NotFoundError(f"strategy_run {run_id} not found", details={"run_id": str(run_id)})

    manager = request.app.state.live_runner_manager
    await manager.stop(run_id)
    updated = await runs_store.get(db, run_id)
    return _row_to_record(updated or run)


@router.get("/strategy_runs", response_model=list[StrategyRunRecord])
async def list_strategy_runs(
    db: DBConn,
    user: Annotated[User, Depends(get_current_user)],
    status: Annotated[Literal["running", "stopped", "errored"] | None, Query()] = None,
    candidate_id: Annotated[UUID | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 200,
) -> list[StrategyRunRecord]:
    """列出当前账户的 live run（status 传非法值 → FastAPI 自动 422，不静默返空）。

    ``candidate_id``:按候选过滤（策略详情页用）——服务端过滤,不再让前端拉全量
    本地 filter(全局 run 超 limit 后老候选的 run 被整批挤出窗口,详情页假装没跑过)。
    ``limit`` 兜底上限（默认 200，按 started_at DESC 取最近）——防 run 历史无界增长后
    dashboard 6s 轮询全量越来越重。
    """
    account_id = account_id_from_user(user)
    rows = await runs_store.list_by_account(
        db, account_id, status=status, candidate_id=candidate_id, limit=limit
    )
    return [_row_to_record(r) for r in rows]


@router.get("/strategy_runs/{run_id}", response_model=StrategyRunRecord)
async def get_strategy_run(
    db: DBConn,
    user: Annotated[User, Depends(get_current_user)],
    run_id: Annotated[UUID, Path()],
) -> StrategyRunRecord:
    """单条 run 详情（仅限本账户）。

    dashboard 详情页直接查这条，不再拉全列表 ``.find()``——否则超出 list LIMIT（200）的
    历史 run 永远 404（CR major fix）。非本账户 / 不存在统一 404，不泄漏存在性。
    """
    account_id = account_id_from_user(user)
    run = await runs_store.get(db, run_id)
    if run is None or run["account_id"] != account_id:
        raise NotFoundError(f"strategy_run {run_id} not found", details={"run_id": str(run_id)})
    return _row_to_record(run)


@router.get("/strategy_runs/{run_id}/decisions", response_model=list[StrategyRunDecisionRecord])
async def list_strategy_run_decisions(
    db: DBConn,
    user: Annotated[User, Depends(get_current_user)],
    run_id: Annotated[UUID, Path()],
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
) -> list[StrategyRunDecisionRecord]:
    """某 run 的决策复盘时间线（仅限本账户）：每根 bar 的下单意图 + 撮合结果。"""
    account_id = account_id_from_user(user)
    run = await runs_store.get(db, run_id)
    if run is None or run["account_id"] != account_id:
        raise NotFoundError(f"strategy_run {run_id} not found", details={"run_id": str(run_id)})
    rows = await runs_store.list_decisions(db, run_id, limit=limit)
    return [_row_to_decision(r) for r in rows]


def _row_to_decision(row: dict[str, Any]) -> StrategyRunDecisionRecord:
    def _f(v: Any) -> float | None:
        return float(v) if v is not None else None

    return StrategyRunDecisionRecord(
        id=row["id"],
        run_id=row["run_id"],
        bar_ts=row["bar_ts"],
        bar_close=float(row["bar_close"]),
        side=row["side"],
        quantity=float(row["quantity"]),
        order_type=row["order_type"],
        limit_price=_f(row.get("limit_price")),
        tag=row.get("tag"),
        intent=row.get("intent"),
        outcome=row["outcome"],
        fill_price=_f(row.get("fill_price")),
        fee=_f(row.get("fee")),
        closed_profit_abs=_f(row.get("closed_profit_abs")),
        closed_profit_pct=_f(row.get("closed_profit_pct")),
        plan_id=row.get("plan_id"),
        order_id=row.get("order_id"),
        reason=row.get("reason"),
        created_at=row["created_at"],
    )


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
        trading_mode=row.get("trading_mode") or "spot",
        leverage=int(row.get("leverage") or 1),
        allocation=(float(row["allocation"]) if row.get("allocation") is not None else None),
        last_bar_ts=row.get("last_bar_ts"),
        cumulative_pnl=float(row["cumulative_pnl"]),
        accounting_status=row.get("accounting_status", "legacy_unverified"),
        accounting_note=row.get("accounting_note"),
        original_cumulative_pnl=row.get("original_cumulative_pnl"),
        run_log=row.get("run_log") or [],
        factor_baseline=row.get("factor_baseline"),
        factor_alerts=row.get("factor_alerts") or {},
        started_at=row["started_at"],
        stopped_at=row.get("stopped_at"),
    )


async def _owned_run(db: Any, user: User, run_id: UUID) -> dict[str, Any]:
    """Validate the human JWT owner before any wallet lookup."""
    row = await runs_store.get(db, run_id)
    if row is None or row["account_id"] != account_id_from_user(user):
        raise NotFoundError("Strategy run not found")
    return row


@router.get("/strategy_runs/{run_id}/wallet")
async def get_run_wallet(
    run_id: UUID,
    db: DBConn,
    user: Annotated[User, Depends(get_current_user)],
    settings: Annotated[PaperSettings, Depends(get_paper_settings)],
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    """Owner-scoped equity, exact book positions and net PnL components."""
    from ..wallet_accounting import snapshot

    run = await _owned_run(db, user, run_id)
    token = authorization.removeprefix("Bearer ").strip() if authorization else None
    return await snapshot(db, run, settings, token)


@router.get("/strategy_runs/{run_id}/positions")
async def get_run_positions(
    run_id: UUID, db: DBConn, user: Annotated[User, Depends(get_current_user)]
) -> list[dict[str, Any]]:
    """Never substitute the legacy account's same-symbol position."""
    run = await _owned_run(db, user, run_id)
    return await positions_store.list_by_account(db, run["account_id"], run_id=run_id)


@router.post("/strategy_runs/{run_id}/release_capital")
async def release_run_capital(
    run_id: UUID,
    db: DBConn,
    user: Annotated[User, Depends(get_current_user)],
    settings: Annotated[PaperSettings, Depends(get_paper_settings)],
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    """Release a stopped flat book once; never implicitly close a position."""
    run = await _owned_run(db, user, run_id)
    wallet = await wallets_store.get(db, run["account_id"], run_id)
    if wallet is None:
        raise wallets_store.WalletConflict("Legacy runs have no wallet")
    if wallet["released_at"] is not None:
        return wallet
    token = authorization.removeprefix("Bearer ").strip() if authorization else None
    async with DataClient(settings.data_service_url, token or "") as dc:
        converter = BaseCurrencyConverter(wallet["base_currency"], dc)
        for currency in wallet["cash_balances"]:
            await converter.rate(currency)
    async with db.transaction():
        return await wallets_store.release(db, run, converter.offline_copy())


@router.post("/strategy_runs/{run_id}/resume", response_model=StrategyRunRecord)
async def resume_strategy_run(
    run_id: UUID,
    request: Request,
    db: DBConn,
    background_tasks: BackgroundTasks,
    user: Annotated[User, Depends(get_current_user)],
    settings: Annotated[PaperSettings, Depends(get_paper_settings)],
) -> StrategyRunRecord:
    """Explicitly resume the same owned book; never adopt legacy holdings or allocate again."""
    run = await _owned_run(db, user, run_id)
    if run["accounting_status"] != "verified":
        raise wallets_store.WalletConflict("Legacy runs cannot resume automatic management")
    candidate = await candidates_store.get_candidate(db, run["candidate_id"])
    if (
        not candidate
        or candidate["status"] != "promoted"
        or candidate["owner_account_id"] != run["account_id"]
    ):
        raise CandidateNotOwnedError("Resume requires your own promoted candidate")
    async with db.transaction():
        await wallets_store.lock(db, run)
        current = await runs_store.get(db, run_id)
        if current is None or current["status"] not in ("stopped", "errored"):
            raise wallets_store.WalletConflict("Only a stopped or errored book can resume")
        active = await runs_store.list_by_account(db, run["account_id"], status="running")
        if len(active) >= settings.live_max_running_runs_per_account:
            raise wallets_store.WalletConflict("Active run limit reached")
        if any(row["candidate_id"] == run["candidate_id"] for row in active):
            raise wallets_store.WalletConflict("Candidate already has an active run")
        await runs_store.set_status(db, run_id, "running", only_if_status=current["status"])
        await runs_store.append_log(db, run_id, "info", "用户恢复原运行钱包；未重新划拨资金")
        updated = await runs_store.get(db, run_id)
        assert updated is not None
    background_tasks.add_task(request.app.state.live_runner_manager.start_async, updated)
    return _row_to_record(updated)
