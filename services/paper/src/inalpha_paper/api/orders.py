"""``POST /orders/submit`` + GET /orders + GET /positions + GET /accounts/me。

D-8b 升级：
- 订单 / 持仓 / 现金全部落 Postgres
- 按 ``account_id``（= JWT sub 派生 UUID）隔离
- POST /orders/submit 的写 orders + 更新 positions + 扣 cash 在**一个事务**里
- 新增 list/query 端点（GET /orders, GET /positions, GET /accounts/me）

设计动机见 [ADR-0012](../../../../docs/decisions/0012-plan-exec-separation.md)。

撮合细节没变（[OrderExecutor](../execution/order_executor.py)）：
- ``ref_price`` optional，省略时服务端调 data /ticker 自取最新价
"""
from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Query, Request
from inalpha_shared.auth import User, get_current_user
from inalpha_shared.db import DBConn
from inalpha_shared.errors import InalphaError, UnauthorizedError

from ..account_id import account_id_from_user
from ..config import PaperSettings, get_paper_settings
from ..data_client import DataClient
from ..execution import risk_guard as risk_guard_mod
from ..execution.order_executor import OrderExecutor
from ..schemas import (
    AccountSnapshot,
    OrderRecord,
    PositionRecord,
    SubmitOrderRequest,
    SubmitOrderResponse,
)
from ..storage import accounts as accounts_store
from ..storage import orders as orders_store
from ..storage import positions as positions_store

router = APIRouter(tags=["orders"])


class RefPriceUnavailableError(InalphaError):
    code = "REF_PRICE_UNAVAILABLE"
    status_code = 400


@router.post("/orders/submit", response_model=SubmitOrderResponse)
async def post_submit_order(
    req: SubmitOrderRequest,
    request: Request,
    db: DBConn,
    settings: Annotated[PaperSettings, Depends(get_paper_settings)],
    user: Annotated[User, Depends(get_current_user)],
    authorization: Annotated[str | None, Header()] = None,
) -> SubmitOrderResponse:
    """单笔下单（D-8b：落盘 + 持仓更新 + 扣现金事务）。

    D-9（issue #3）：撮合前先过 RiskGuard 拦截（``risk_engine_enabled=False`` 时 fail-open）。
    D-9.1a（issue #8）：RiskGuard 改 per-account（factory.get_for_check(account_id)）。
    """
    account_id = account_id_from_user(user)

    # D-9 风控前置闸门：违规 → 409 RISK_REJECTED + risk_locks 表写新行
    # enforce 内部用独立 connection 写锁，不复用 db（避免后续异常导致锁回滚）
    factory = getattr(request.app.state, "risk_guard_factory", None)
    await risk_guard_mod.enforce(
        factory,
        account_id=account_id,
        venue=req.venue,
        symbol=req.symbol,
        side=req.side,
    )

    ref_price = await _resolve_ref_price(req, settings, authorization)

    # 算成交（纯函数，不依赖 DB）
    result = OrderExecutor.execute(
        venue=req.venue,
        symbol=req.symbol,
        side=req.side,
        order_type=req.order_type,
        quantity=req.quantity,
        price=req.price,
        ref_price=ref_price,
        fee_rate=req.fee_rate,
    )

    # 落盘 + 持仓 + 现金（事务）
    async with db.transaction():
        await accounts_store.get_or_create(db, account_id)

        await orders_store.insert(
            db,
            account_id=account_id,
            client_order_id=result["client_order_id"],  # type: ignore[arg-type]
            venue=req.venue,
            symbol=req.symbol,
            side=req.side,
            order_type=req.order_type,
            quantity=req.quantity,
            price=req.price,
            status=result["status"],  # type: ignore[arg-type]
            filled_quantity=result["filled_quantity"],  # type: ignore[arg-type]
            avg_fill_price=result["avg_fill_price"],  # type: ignore[arg-type]
            fee=result["fee"],  # type: ignore[arg-type]
            notional=result["notional"],  # type: ignore[arg-type]
            ts_event=result["ts_event"],  # type: ignore[arg-type]
        )

        if result["status"] == "FILLED":
            await _apply_fill_to_positions_and_cash(
                db,
                account_id=account_id,
                venue=req.venue,
                symbol=req.symbol,
                side=req.side,
                quantity=Decimal(str(result["filled_quantity"])),
                fill_price=Decimal(str(result["avg_fill_price"])),
                fee=Decimal(str(result["fee"])),
            )

    return SubmitOrderResponse(**result)  # type: ignore[arg-type]


@router.get("/orders", response_model=list[OrderRecord])
async def list_orders(
    db: DBConn,
    user: Annotated[User, Depends(get_current_user)],
    symbol: Annotated[str | None, Query()] = None,
    status: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> list[OrderRecord]:
    """列出当前用户的订单流水，按 ts_event DESC。"""
    account_id = account_id_from_user(user)
    rows = await orders_store.list_by_account(
        db, account_id, symbol=symbol, status=status, limit=limit
    )
    return [_row_to_order_record(r) for r in rows]


@router.get("/positions", response_model=list[PositionRecord])
async def list_positions(
    db: DBConn,
    user: Annotated[User, Depends(get_current_user)],
    include_flat: Annotated[bool, Query()] = False,
) -> list[PositionRecord]:
    """列出当前用户活跃持仓（quantity!=0）。"""
    account_id = account_id_from_user(user)
    rows = await positions_store.list_by_account(db, account_id, include_flat=include_flat)
    return [PositionRecord(**_decimal_to_float(r)) for r in rows]


@router.get("/accounts/me", response_model=AccountSnapshot)
async def get_my_account(
    db: DBConn,
    user: Annotated[User, Depends(get_current_user)],
) -> AccountSnapshot:
    """当前账户快照：cash + 持仓估值（按 avg_open_price）+ 累计 realized PnL。"""
    account_id = account_id_from_user(user)
    acct = await accounts_store.get_or_create(db, account_id)

    pos_rows = await positions_store.list_by_account(db, account_id, include_flat=False)
    positions_value = Decimal(0)
    realized_pnl = Decimal(0)
    for p in pos_rows:
        positions_value += Decimal(p["quantity"]) * Decimal(p["avg_open_price"])
        realized_pnl += Decimal(p["realized_pnl"])

    cash = Decimal(acct["cash"])
    return AccountSnapshot(
        account_id=str(acct["account_id"]),
        initial_cash=float(Decimal(acct["initial_cash"])),
        cash=float(cash),
        positions_value=float(positions_value),
        total_equity=float(cash + positions_value),
        realized_pnl=float(realized_pnl),
        created_at=acct["created_at"],
        updated_at=acct["updated_at"],
    )


# ────────────────────────────────────────────────────────────────────
# 内部辅助
# ────────────────────────────────────────────────────────────────────


async def _resolve_ref_price(
    req: SubmitOrderRequest,
    settings: PaperSettings,
    authorization: str | None,
) -> float:
    """req.ref_price 没给时调 data /ticker。"""
    if req.ref_price is not None:
        return req.ref_price
    if not authorization or not authorization.startswith("Bearer "):
        raise UnauthorizedError("missing Authorization header")
    user_token = authorization.removeprefix("Bearer ").strip()
    async with DataClient(settings.data_service_url, user_token) as data_client:
        try:
            ticker = await data_client.get_ticker(venue=req.venue, symbol=req.symbol)
        except Exception as e:
            raise RefPriceUnavailableError(
                f"failed to fetch ref_price for {req.symbol}@{req.venue}: {e}",
                details={"venue": req.venue, "symbol": req.symbol},
            ) from e
    return float(ticker["price"])


async def _apply_fill_to_positions_and_cash(
    db: Any,
    *,
    account_id: Any,
    venue: str,
    symbol: str,
    side: str,
    quantity: Decimal,
    fill_price: Decimal,
    fee: Decimal,
) -> None:
    """一笔 fill 同时更新 positions + cash（在调用方的事务里）。"""
    notional = quantity * fill_price
    # BUY: -notional - fee；SELL: +notional - fee
    cash_delta = (-notional if side == "BUY" else notional) - fee
    await accounts_store.apply_cash_delta(db, account_id, cash_delta)
    await positions_store.apply_fill(
        db,
        account_id=account_id,
        venue=venue,
        symbol=symbol,
        side=side,
        fill_qty=quantity,
        fill_price=fill_price,
    )


def _row_to_order_record(row: dict[str, Any]) -> OrderRecord:
    return OrderRecord(
        client_order_id=row["client_order_id"],
        venue=row.get("venue"),
        symbol=row.get("symbol"),
        side=row["side"],
        type=row["type"],
        quantity=float(row["quantity"]),
        price=float(row["price"]) if row.get("price") is not None else None,
        status=row["status"],
        filled_quantity=float(row["filled_quantity"]),
        avg_fill_price=(
            float(row["avg_fill_price"]) if row.get("avg_fill_price") is not None else None
        ),
        fee=float(row["fee"]) if row.get("fee") is not None else None,
        notional=float(row["notional"]) if row.get("notional") is not None else None,
        ts_event=row["ts_event"],
        ts_init=row["ts_init"],
        trade_plan_id=str(row["trade_plan_id"]) if row.get("trade_plan_id") else None,
    )


def _decimal_to_float(row: dict[str, Any]) -> dict[str, Any]:
    out = dict(row)
    for k in ("quantity", "avg_open_price", "realized_pnl"):
        if k in out and out[k] is not None:
            out[k] = float(out[k])
    return out
