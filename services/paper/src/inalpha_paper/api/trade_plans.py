"""Plan/Exec 路由（D-8b：从 orchestration 进程内 PlanStore 迁库到 paper service）。

5 个端点：

- POST /plans                —— 创建（pending_approval）
- GET  /plans                —— 列出当前用户的 plans
- GET  /plans/{plan_id}      —— 查单个 plan
- POST /plans/{plan_id}/approve —— 审批 + 发放一次性 approval_token
- POST /plans/{plan_id}/reject  —— 拒绝（终态）
- POST /plans/{plan_id}/execute —— 凭 token 真下单（一个事务：消费 token + 撮合 + 落盘
                                    + 更新 positions/cash + 切 plan 状态）

设计动机见 [ADR-0012](../../../../docs/decisions/0012-plan-exec-separation.md)。
"""
from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Path, Query
from inalpha_shared.auth import User, get_current_user
from inalpha_shared.db import DBConn
from inalpha_shared.errors import InalphaError

from ..account_id import account_id_from_user
from ..config import PaperSettings, get_paper_settings
from ..data_client import DataClient
from ..execution.order_executor import OrderExecutor
from ..schemas import (
    ApprovePlanRequest,
    CreatePlanRequest,
    ExecutePlanRequest,
    ExecutePlanResponse,
    PlanRecord,
    RejectPlanRequest,
    SubmitOrderResponse,
)
from ..storage import accounts as accounts_store
from ..storage import orders as orders_store
from ..storage import positions as positions_store
from ..storage import trade_plans as plans_store
from ..storage.trade_plans import PlanError

router = APIRouter(tags=["plans"])


class PlanHttpError(InalphaError):
    """把 PlanError code 透到 HTTP 错误响应里。"""

    code = "PLAN_ERROR"
    status_code = 400


def _raise_plan_http(err: PlanError) -> None:
    """PlanError → InalphaError（让 install_error_handler 接管成 400）。"""
    raised = PlanHttpError(err.message, code=err.code, details=err.details)
    raise raised from err


# ────────────────────────────────────────────────────────────────────
# 创建
# ────────────────────────────────────────────────────────────────────


@router.post("/plans", response_model=PlanRecord)
async def create_plan(
    req: CreatePlanRequest,
    db: DBConn,
    user: Annotated[User, Depends(get_current_user)],
) -> PlanRecord:
    account_id = account_id_from_user(user)
    order_params: dict[str, Any] = {
        "side": req.side,
        "type": req.order_type,
        "quantity": req.quantity,
    }
    if req.price is not None:
        order_params["price"] = req.price

    try:
        row = await plans_store.create(
            db,
            account_id=account_id,
            intent=req.intent,
            venue=req.venue,
            symbol=req.symbol,
            order_params=order_params,
            rationale=req.rationale,
            expire_in_seconds=req.expire_in_seconds,
        )
    except PlanError as e:
        _raise_plan_http(e)
        raise  # mypy

    return _row_to_plan_record(row)


# ────────────────────────────────────────────────────────────────────
# 查询
# ────────────────────────────────────────────────────────────────────


@router.get("/plans", response_model=list[PlanRecord])
async def list_plans(
    db: DBConn,
    user: Annotated[User, Depends(get_current_user)],
    status: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> list[PlanRecord]:
    account_id = account_id_from_user(user)
    rows = await plans_store.list_by_account(db, account_id, status=status, limit=limit)
    return [_row_to_plan_record(r) for r in rows]


@router.get("/plans/{plan_id}", response_model=PlanRecord)
async def get_plan(
    plan_id: Annotated[UUID, Path()],
    db: DBConn,
    user: Annotated[User, Depends(get_current_user)],
) -> PlanRecord:
    account_id = account_id_from_user(user)
    row = await plans_store.get(db, account_id=account_id, plan_id=plan_id)
    if row is None:
        raise PlanHttpError(f"plan {plan_id} not found", code="PLAN_NOT_FOUND", details={"planId": str(plan_id)})
    return _row_to_plan_record(row)


# ────────────────────────────────────────────────────────────────────
# 审批 / 拒绝
# ────────────────────────────────────────────────────────────────────


@router.post("/plans/{plan_id}/approve", response_model=PlanRecord)
async def approve_plan(
    plan_id: Annotated[UUID, Path()],
    req: ApprovePlanRequest,
    db: DBConn,
    user: Annotated[User, Depends(get_current_user)],
) -> PlanRecord:
    """审批 plan，返回含 approval_token 的完整行。

    实现细节：``plans_store.approve`` 已经做了 UPDATE...RETURNING + 返回新 token；
    我们直接补 get 来拿完整字段，但**保留 approve 返回的 approval_token**
    （get 的 SELECT 不返这一列，避免在响应里泄露给非 approve 路径的调用方）。
    """
    account_id = account_id_from_user(user)
    try:
        approved = await plans_store.approve(
            db, account_id=account_id, plan_id=plan_id, approver=req.approver
        )
    except PlanError as e:
        _raise_plan_http(e)
        raise  # mypy

    # 拿完整字段后再把 token 合并回来（approve 返回的 dict 不包含 venue/symbol/...）
    row = await plans_store.get(db, account_id=account_id, plan_id=plan_id)
    assert row is not None
    row = {**row, "approval_token": approved["approval_token"]}
    return _row_to_plan_record(row)


@router.post("/plans/{plan_id}/reject", response_model=PlanRecord)
async def reject_plan(
    plan_id: Annotated[UUID, Path()],
    req: RejectPlanRequest,
    db: DBConn,
    user: Annotated[User, Depends(get_current_user)],
) -> PlanRecord:
    account_id = account_id_from_user(user)
    try:
        await plans_store.reject(
            db,
            account_id=account_id,
            plan_id=plan_id,
            reason=req.reason,
            rejector=req.rejector,
        )
    except PlanError as e:
        _raise_plan_http(e)

    row = await plans_store.get(db, account_id=account_id, plan_id=plan_id)
    assert row is not None
    return _row_to_plan_record(row)


# ────────────────────────────────────────────────────────────────────
# 执行 —— 一个事务：消费 token + 撮合 + 落盘 + 持仓 + 切状态
# ────────────────────────────────────────────────────────────────────


@router.post("/plans/{plan_id}/execute", response_model=ExecutePlanResponse)
async def execute_plan(
    plan_id: Annotated[UUID, Path()],
    req: ExecutePlanRequest,
    db: DBConn,
    settings: Annotated[PaperSettings, Depends(get_paper_settings)],
    user: Annotated[User, Depends(get_current_user)],
    authorization: Annotated[str | None, Header()] = None,
) -> ExecutePlanResponse:
    """凭 approval_token 把 plan 执行成订单。

    顺序（D-8b' review 高风险 #3 修复后）：

    1. 读 plan（**不消费 token**）拿 venue/symbol —— 失败可重试
    2. 取 refPrice（网络调用，**不消费 token**）—— REF_PRICE_UNAVAILABLE 时
       token 仍有效，caller backfill 后再来即可
    3. OrderExecutor 算成交（pure）
    4. **单一事务**：consume_approval → orders insert → positions/cash
       → record_execution；任何一步失败回滚 token 也回滚（保持 'approved' 可重试）

    旧 bug：consume_approval 跑在独立事务里、ticker 在 try 外；ticker / order
    insert 任一失败让 plan 永远卡在 'approved' + 无 token —— 再 execute 报
    INVALID_TOKEN，再 approve 报 INVALID_STATE，整个 plan 死锁。
    """
    account_id = account_id_from_user(user)

    # 1. 读 plan（不消费 token）取 venue/symbol/order_params
    plan_row = await plans_store.get(db, account_id=account_id, plan_id=plan_id)
    if plan_row is None:
        raise PlanHttpError(
            f"plan {plan_id} not found",
            code="PLAN_NOT_FOUND",
            details={"planId": str(plan_id)},
        )
    if plan_row["status"] != "approved":
        raise PlanHttpError(
            f"plan {plan_id} status={plan_row['status']!r} (must be 'approved' to execute)",
            code="INVALID_STATE",
            details={"planId": str(plan_id), "status": plan_row["status"]},
        )

    order_params: dict[str, Any] = plan_row["order_params"]
    venue: str = plan_row["venue"]
    symbol: str = plan_row["symbol"]
    side: str = order_params["side"]
    order_type: str = order_params["type"]
    quantity: float = float(order_params["quantity"])
    price: float | None = order_params.get("price")

    # 2. 取 refPrice（不消费 token —— 网络失败可让 caller 重试）
    if not authorization or not authorization.startswith("Bearer "):
        raise PlanHttpError("missing Authorization header", code="UNAUTHORIZED")
    user_token = authorization.removeprefix("Bearer ").strip()
    async with DataClient(settings.data_service_url, user_token) as data_client:
        try:
            ticker = await data_client.get_ticker(venue=venue, symbol=symbol)
        except Exception as e:
            raise PlanHttpError(
                f"failed to fetch ref_price for {symbol}@{venue}: {e}",
                code="REF_PRICE_UNAVAILABLE",
                details={"venue": venue, "symbol": symbol, "planId": str(plan_id)},
            ) from e
    ref_price = float(ticker["price"])

    # 3. 撮合（pure）
    result = OrderExecutor.execute(
        venue=venue,
        symbol=symbol,
        side=side,  # type: ignore[arg-type]
        order_type=order_type,  # type: ignore[arg-type]
        quantity=quantity,
        price=price,
        ref_price=ref_price,
        fee_rate=0.001,  # D-8b：从 plan 里读 fee 留 D-9
    )

    # 4. 单一事务：consume_token + 撮合落盘 + 切 executed（失败一起回滚）
    #    consume_approval 原子检查 token+status+expire，并发或重放都在这里 fail。
    async with db.transaction():
        try:
            await plans_store.consume_approval(
                db,
                account_id=account_id,
                plan_id=plan_id,
                approval_token=req.approval_token,
            )
        except PlanError as e:
            _raise_plan_http(e)
            raise  # mypy
        await accounts_store.get_or_create(db, account_id)
        await orders_store.insert(
            db,
            account_id=account_id,
            client_order_id=result["client_order_id"],  # type: ignore[arg-type]
            venue=venue,
            symbol=symbol,
            side=side,
            order_type=order_type,
            quantity=quantity,
            price=price,
            status=result["status"],  # type: ignore[arg-type]
            filled_quantity=result["filled_quantity"],  # type: ignore[arg-type]
            avg_fill_price=result["avg_fill_price"],  # type: ignore[arg-type]
            fee=result["fee"],  # type: ignore[arg-type]
            notional=result["notional"],  # type: ignore[arg-type]
            ts_event=result["ts_event"],  # type: ignore[arg-type]
            trade_plan_id=plan_id,
        )
        if result["status"] == "FILLED":
            notional = Decimal(str(result["notional"]))
            fee = Decimal(str(result["fee"]))
            cash_delta = (-notional if side == "BUY" else notional) - fee
            await accounts_store.apply_cash_delta(db, account_id, cash_delta)
            await positions_store.apply_fill(
                db,
                account_id=account_id,
                venue=venue,
                symbol=symbol,
                side=side,
                fill_qty=Decimal(str(result["filled_quantity"])),
                fill_price=Decimal(str(result["avg_fill_price"])),
            )
        # plan 切 executed（即使 result.status=REJECTED 也 executed —— "已尝试"是事实）
        await plans_store.record_execution(
            db, plan_id=plan_id, resulting_order_id=result["client_order_id"]  # type: ignore[arg-type]
        )

    return ExecutePlanResponse(
        plan_id=str(plan_id),
        plan_status="executed",
        order=SubmitOrderResponse(**result),  # type: ignore[arg-type]
    )


# ────────────────────────────────────────────────────────────────────
# 内部
# ────────────────────────────────────────────────────────────────────


def _row_to_plan_record(row: dict[str, Any]) -> PlanRecord:
    return PlanRecord(
        plan_id=str(row["plan_id"]),
        account_id=str(row["account_id"]) if row.get("account_id") else None,
        intent=row["intent"],
        venue=row["venue"],
        symbol=row["symbol"],
        order_params=row["order_params"],
        risk_params=row.get("risk_params", {}),
        rationale=row["rationale"],
        status=row["status"],
        approval_token=row.get("approval_token"),
        approved_by=row.get("approved_by"),
        rejection_reason=row.get("rejection_reason"),
        created_at=row["created_at"],
        approved_at=row.get("approved_at"),
        executed_at=row.get("executed_at"),
        expire_at=row["expire_at"],
        resulting_order_id=row.get("resulting_order_id"),
    )
