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

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Query, Request
from inalpha_shared.auth import User, get_current_user
from inalpha_shared.db import DBConn
from inalpha_shared.errors import InalphaError, UnauthorizedError

from ..account_id import account_id_from_user
from ..config import PaperSettings, get_paper_settings
from ..data_client import DataClient
from ..execution import perp_margin
from ..execution import risk_guard as risk_guard_mod
from ..execution.currency_resolver import resolve_currency
from ..execution.order_executor import OrderExecutor
from ..execution.spot_guard import InsufficientPositionError, violates_spot_long_only
from ..fills import apply_fill_to_positions_and_cash
from ..fx import BaseCurrencyConverter
from ..fx import needs_network as fx_needs_network
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

    # perp 资格硬 gate(trading_mode=perp 须 crypto + USDT-M 永续标的 + 杠杆 1..20,
    # 否则 422 PERP_NOT_ELIGIBLE;spot 放行)。不静默降级。
    perp_margin.validate_perp_eligibility(
        venue=req.venue, symbol=req.symbol,
        trading_mode=req.trading_mode, leverage=req.leverage,
    )

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

    # 单笔 notional 硬上限（无状态前置校验，issue #42）；超限 → 409 RISK_REJECTED
    risk_guard_mod.check_order_notional(
        factory, quantity=req.quantity, ref_price=ref_price,
        venue=req.venue, symbol=req.symbol,
    )

    # perp 保证金购买力守门(v1 简化:本笔初始保证金 IM=notional/leverage + fee 不超过账户该
    # 计价货币钱包余额;跨仓聚合留 Phase 2)。spot SELL 守门在下方事务内 FOR UPDATE 锁行做
    # (TOCTOU 硬化);perp 做空合法,由本钱包购买力校验放行。
    if req.trading_mode == "perp":
        acct = await accounts_store.get_or_create(db, account_id)
        currency = resolve_currency(req.venue, req.symbol)
        wallet = Decimal(str((acct.get("cash_balances") or {}).get(currency, "0")))
        im = Decimal(str(req.quantity * ref_price / req.leverage))
        fee_amt = Decimal(str(req.quantity * ref_price * req.fee_rate))
        if im + fee_amt > wallet:
            raise InalphaError(
                f"perp 保证金不足:需 IM {im} + fee {fee_amt} 超钱包 {wallet} {currency}",
                code="INSUFFICIENT_MARGIN", status_code=409,
                details={"im": str(im), "fee": str(fee_amt),
                         "wallet": str(wallet), "currency": currency},
            )

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

        # 现货 long-only 守门（与回测 Portfolio.can_afford_sell 同口径）：OrderExecutor
        # 无状态、apply_fill 又允许负仓——不拦则裸空 / 超卖翻空落账成凭空做空的负仓。
        # **必须在本事务内 FOR UPDATE 锁行再校验**：否则两个并发 SELL 各读旧持仓双双过闸
        # （TOCTOU）→ 把持仓打成负仓。raise 触发事务回滚 → 不落单/不落账（409 不变）。
        if req.side == "SELL":
            cur_pos = await positions_store.get(
                db, account_id=account_id, venue=req.venue, symbol=req.symbol,
                for_update=True,
            )
            current_qty = Decimal(str(cur_pos["quantity"])) if cur_pos else Decimal(0)
            if violates_spot_long_only(
                side=req.side, quantity=req.quantity, current_qty=current_qty,
                trading_mode=req.trading_mode,
            ):
                raise InsufficientPositionError(
                    f"SELL {req.quantity} exceeds current position {current_qty} "
                    "(spot long-only: short-selling not permitted)",
                    details={"venue": req.venue, "symbol": req.symbol,
                             "requested": req.quantity, "current_qty": str(current_qty)},
                )

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
            trading_mode=req.trading_mode,
            leverage=req.leverage,
        )

        if result["status"] == "FILLED":
            ts_event = result["ts_event"]
            order_id = result["client_order_id"]
            assert isinstance(ts_event, datetime)
            assert isinstance(order_id, str)
            realized_pnl = await apply_fill_to_positions_and_cash(
                db,
                account_id=account_id,
                venue=req.venue,
                symbol=req.symbol,
                side=req.side,
                quantity=Decimal(str(result["filled_quantity"])),
                fill_price=Decimal(str(result["avg_fill_price"])),
                fee=Decimal(str(result["fee"])),
                ts_event=ts_event,
                order_id=order_id,
                trading_mode=req.trading_mode,
                leverage=req.leverage,
            )
            # 回写这笔成交的已实现盈亏(开仓单 0 / 平仓单实现盈亏)——每笔交易记录都带盈亏。
            await orders_store.set_realized_pnl(
                db, client_order_id=order_id, realized_pnl=realized_pnl
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
    settings: Annotated[PaperSettings, Depends(get_paper_settings)],
    user: Annotated[User, Depends(get_current_user)],
    authorization: Annotated[str | None, Header()] = None,
) -> AccountSnapshot:
    """当前账户快照：多币种 cash 桶 + 持仓估值（按 avg_open_price），折算到 base_currency。

    D-11：cash / 持仓可能跨币种，按 base_currency FX 折算汇总。本地可解析的币种
    （同币种 / USD 稳定币）不打网络；其余调 data ``/fx``，拿不到的币种排除 + warning。
    """
    account_id = account_id_from_user(user)
    acct = await accounts_store.get_or_create(db, account_id)
    base_currency = acct["base_currency"]

    # include_flat=True：已平仓行（quantity=0）对 positions_value 贡献 0，但仍携带累计
    # realized_pnl——纳入才能让账户总已实现盈亏完整（CR：避免漏计已平仓持仓的 PnL）。
    pos_rows = await positions_store.list_by_account(db, account_id, include_flat=True)

    # 原始按币种桶
    cash_balances: dict[str, Decimal] = {
        cur: Decimal(str(amt)) for cur, amt in (acct["cash_balances"] or {}).items()
    }
    # 持仓估值（按 avg_open_price）+ realized_pnl，都按计价货币分桶
    # （NULL 行按 venue/symbol 兜底解析），稍后用同一个 converter 折算到 base。
    pos_value_by_ccy: dict[str, Decimal] = {}
    realized_pnl_by_ccy: dict[str, Decimal] = {}
    for p in pos_rows:
        ccy = p.get("currency") or resolve_currency(
            p["venue"], p["symbol"], default=base_currency
        )
        value = Decimal(p["quantity"]) * Decimal(p["avg_open_price"])
        pos_value_by_ccy[ccy] = pos_value_by_ccy.get(ccy, Decimal(0)) + value
        realized_pnl_by_ccy[ccy] = (
            realized_pnl_by_ccy.get(ccy, Decimal(0)) + Decimal(p["realized_pnl"])
        )

    # 只在存在非本地可解析币种时才开 DataClient（单币种 / crypto-USD 账户零网络）
    all_ccys = set(cash_balances) | set(pos_value_by_ccy) | set(realized_pnl_by_ccy)
    # token 实际上必非空：get_current_user 依赖已保证 Bearer header 合法，否则先行 401；
    # 这里 token=None 分支是防御性的（理论不可达），保留以防未来调用方绕过 auth。
    token = (
        authorization.removeprefix("Bearer ").strip()
        if authorization and authorization.startswith("Bearer ")
        else None
    )
    data_client = (
        DataClient(settings.data_service_url, token)
        if token and fx_needs_network(all_ccys, base_currency)
        else None
    )
    # try 前初始化，确保即便 convert() 抛非预期异常也不会在 return 处 NameError（CR）
    fx_warnings: list[str] = []
    try:
        converter = BaseCurrencyConverter(base_currency, data_client)
        cash_base = Decimal(0)
        for cur, amt in cash_balances.items():
            converted = await converter.convert(amt, cur)
            if converted is not None:
                cash_base += converted
        positions_base = Decimal(0)
        for cur, amt in pos_value_by_ccy.items():
            converted = await converter.convert(amt, cur)
            if converted is not None:
                positions_base += converted
        # realized_pnl 同样按币种折算（汇率已缓存，无额外网络）；FX 不可用的币种排除
        realized_pnl_base = Decimal(0)
        for cur, amt in realized_pnl_by_ccy.items():
            converted = await converter.convert(amt, cur)
            if converted is not None:
                realized_pnl_base += converted
        fx_warnings = converter.warnings
    finally:
        if data_client is not None:
            await data_client.close()

    return AccountSnapshot(
        account_id=str(acct["account_id"]),
        base_currency=base_currency,
        initial_cash=float(Decimal(acct["initial_cash"])),
        cash=float(cash_base),
        cash_balances={cur: float(amt) for cur, amt in cash_balances.items()},
        positions_value=float(positions_base),
        total_equity=float(cash_base + positions_base),
        realized_pnl=float(realized_pnl_base),
        fx_warnings=fx_warnings,
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
        realized_pnl=(
            float(row["realized_pnl"]) if row.get("realized_pnl") is not None else None
        ),
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
