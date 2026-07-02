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
from ..execution.currency_resolver import KNOWN_CASH_CURRENCIES, resolve_currency
from ..execution.order_executor import OrderExecutor
from ..execution.spot_guard import (
    InsufficientCashError,
    InsufficientPositionError,
    violates_spot_buying_power,
    violates_spot_long_only,
)
from ..fills import apply_fill_to_positions_and_cash
from ..fx import BaseCurrencyConverter, convert_cash_balances
from ..fx import needs_network as fx_needs_network
from ..schemas import (
    AccountSnapshot,
    CashFlowRecord,
    DepositRequest,
    OrderRecord,
    PositionRecord,
    ResetAccountRequest,
    SubmitOrderRequest,
    SubmitOrderResponse,
)
from ..storage import accounts as accounts_store
from ..storage import closed_trades as closed_trades_store
from ..storage import orders as orders_store
from ..storage import positions as positions_store
from ..storage import strategy_runs as runs_store

router = APIRouter(tags=["orders"])


class RefPriceUnavailableError(InalphaError):
    code = "REF_PRICE_UNAVAILABLE"
    status_code = 400


class AccountHasRunningRunsError(InalphaError):
    """reset 前须先停掉所有 running run(否则 runner 下一根 bar 又把仓开回来)。"""

    code = "ACCOUNT_HAS_RUNNING_RUNS"
    status_code = 409


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
        # **恒锁账户行,统一全局锁序 accounts → positions**:
        # - spot BUY 购买力守门 / perp 跨仓保证金聚合都要"读钱包+其他仓 IM → 校验 →
        #   落账"原子——perp 若不锁账户行,两笔并发开仓在**不同 symbol**(锁不同持仓
        #   行,互不阻塞)会各读旧 others_im/钱包双双过闸,合计 IM 超钱包(恰是跨仓
        #   聚合要堵的洞);
        # - 与 deposit/reset(先锁 accounts 再动 positions)同序,消除 spot SELL 先锁
        #   positions 再扣 cash 的反序死锁窗口。
        # 代价:同账户下单串行化——模拟盘量级可接受。
        acct = await accounts_store.get_or_create(db, account_id, for_update=True)

        # perp 保证金购买力守门（**事务内 FOR UPDATE**，与下方 spot SELL 守门同口径防 TOCTOU：
        # 否则同账户同标的并发 BUY 各读旧 wallet/持仓双双过闸 → double-open、累计 IM 超钱包）。
        # 与回测 Portfolio.can_afford_buy/sell 同口径：按**成交后目标仓**算 prospective IM =
        # |cur_qty ± qty| × price / leverage——平 / 减仓 IM 降不误拒 cover;开 / 加 / 反手按目标
        # 仓校验。**跨仓聚合(#114)**:其他活跃 perp 仓已占 IM(positions.margin_used,
        # 每笔 fill 后重算的权威值)一并计入——多仓合计不得超钱包,堵单笔各自比全钱包的洞。
        # raise 触发回滚 → 不落单/不落账（409）。
        if req.trading_mode == "perp":
            currency = resolve_currency(req.venue, req.symbol)
            wallet = Decimal(str((acct.get("cash_balances") or {}).get(currency, "0")))
            cur_pos = await positions_store.get(
                db, account_id=account_id, venue=req.venue, symbol=req.symbol,
                for_update=True,
            )
            cur_qty = float(cur_pos["quantity"]) if cur_pos else 0.0
            signed_qty = req.quantity if req.side == "BUY" else -req.quantity
            prospective_qty = abs(cur_qty + signed_qty)
            im = Decimal(str(prospective_qty * ref_price / req.leverage))
            fee_amt = Decimal(str(req.quantity * ref_price * req.fee_rate))
            others_im = await positions_store.sum_other_margin_used(
                db, account_id, currency=currency,
                exclude_venue=req.venue, exclude_symbol=req.symbol,
            )
            if others_im + im + fee_amt > wallet:
                raise perp_margin.InsufficientMarginError(
                    f"perp 保证金不足:其他仓已占 IM {others_im} + 本笔目标 IM {im} "
                    f"+ fee {fee_amt} 超钱包 {wallet} {currency}",
                    details={"im": str(im), "others_im": str(others_im),
                             "fee": str(fee_amt),
                             "wallet": str(wallet), "currency": currency},
                )

        # 现货 BUY 购买力守门（与回测 Portfolio.can_afford_buy 同口径,账户聚合层落地）：
        # 各币种现金桶按 FX 折算成 base 总可用现金,notional+fee(折 base)超过可用×0.99
        # 即拒——桶允许为负(= 账户内隐式借计价货币,如 USD 户买 USDT 对),但**总折算
        # 现金不允许被买穿**。账户行已在上方 FOR UPDATE,与扣款同事务防 TOCTOU。
        # FX:USD 稳定币本地 1:1 零网络;其余币种在锁内调 data /fx(模拟盘低并发可接
        # 受);拿不到汇率的桶排除出可用现金、订单计价货币折不了则直接拒(fail-closed)。
        if req.trading_mode != "perp" and req.side == "BUY":
            balances = {
                cur: Decimal(str(amt))
                for cur, amt in (acct.get("cash_balances") or {}).items()
            }
            order_ccy = resolve_currency(req.venue, req.symbol)
            base_ccy = acct["base_currency"]
            fx_token = (
                authorization.removeprefix("Bearer ").strip()
                if authorization and authorization.startswith("Bearer ")
                else None
            )
            fx_client = (
                DataClient(settings.data_service_url, fx_token)
                if fx_token and fx_needs_network({*balances, order_ccy}, base_ccy)
                else None
            )
            try:
                converter = BaseCurrencyConverter(base_ccy, fx_client)
                available = await convert_cash_balances(converter, balances)
                order_ccy_rate = await converter.rate(order_ccy)
            finally:
                if fx_client is not None:
                    await fx_client.close()
            if violates_spot_buying_power(
                side=req.side,
                quantity=req.quantity,
                ref_price=ref_price,
                fee_rate=req.fee_rate,
                order_ccy_rate=order_ccy_rate,
                available_cash_base=available,
                trading_mode=req.trading_mode,
            ):
                fx_note = (
                    f"; FX warnings: {'; '.join(converter.warnings)}"
                    if converter.warnings
                    else ""
                )
                raise InsufficientCashError(
                    f"BUY 所需资金超过账户可用现金:约 {req.quantity * ref_price:.2f} "
                    f"{order_ccy}(含手续费),账户折算可用 {available:.2f} {base_ccy}"
                    f"{fx_note}",
                    details={
                        "venue": req.venue,
                        "symbol": req.symbol,
                        "quantity": req.quantity,
                        "ref_price": ref_price,
                        "order_currency": order_ccy,
                        "available_cash_base": str(available),
                        "base_currency": base_ccy,
                        "fx_warnings": converter.warnings,
                    },
                )

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
    """当前账户快照：多币种 cash 桶 + 持仓 mark-to-market 估值，折算到 base_currency。

    D-11：cash / 持仓可能跨币种，按 base_currency FX 折算汇总。本地可解析的币种
    （同币种 / USD 稳定币）不打网络；其余调 data ``/fx``，拿不到的币种排除 + warning。

    持仓估值按**最新市价**（data ``/ticker`` 缓存价,与 dashboard 持仓表同源;
    ``fresh=false`` 只读缓存不触发慢 backfill）:spot 仓贡献 ``qty × mark``;perp 仓
    cash 即钱包、开仓不动名义,贡献未实现盈亏 ``(mark − avg) × qty``。最新价拿不到 →
    spot 按开仓均价兜底 / perp 记 0,并入 ``fx_warnings`` 不静默(此前恒按开仓均价
    估值,总权益基本恒等于初始资金、不反映浮盈)。
    """
    account_id = account_id_from_user(user)
    acct = await accounts_store.get_or_create(db, account_id)
    base_currency = acct["base_currency"]

    # 持仓行只用于市值(mark-to-market);已实现盈亏改从 closed_trades 汇总(见下)。
    pos_rows = await positions_store.list_by_account(db, account_id, include_flat=True)

    # 原始按币种桶
    cash_balances: dict[str, Decimal] = {
        cur: Decimal(str(amt)) for cur, amt in (acct["cash_balances"] or {}).items()
    }
    pos_ccys: set[str] = set()
    has_open_position = False
    for p in pos_rows:
        pos_ccys.add(
            p.get("currency")
            or resolve_currency(p["venue"], p["symbol"], default=base_currency)
        )
        if Decimal(p["quantity"]) != 0:
            has_open_position = True

    # realized_pnl 以**成交审计源**(closed_trades)为准,按最近一次 reset 起算:
    # 此前从 positions 行汇总——reset 删行后快照凭空归零而 closed_trades 仍在,
    # 两套"已实现盈亏"互相矛盾;统一到 closed_trades + reset epoch 一个口径。
    reset_ts = await accounts_store.last_reset_at(db, account_id)
    realized_rows = await closed_trades_store.sum_realized_grouped(
        db, account_id=account_id, since=reset_ts
    )
    realized_pnl_by_ccy: dict[str, Decimal] = {}
    for r in realized_rows:
        ccy = resolve_currency(r["venue"], r["symbol"], default=base_currency)
        realized_pnl_by_ccy[ccy] = (
            realized_pnl_by_ccy.get(ccy, Decimal(0)) + Decimal(str(r["realized"]))
        )

    # 净外生入金(自最近一次 reset,按币种)——真实收益口径的第三项,防充值算成盈利
    flows_by_ccy = await accounts_store.sum_external_flows_since_reset(db, account_id)

    # DataClient 在两种情况下才开：FX 有非本地可解析币种,或有持仓要取 mark
    # （无持仓的单币种 / crypto-USD 账户保持零网络）。
    all_ccys = (
        set(cash_balances) | pos_ccys | set(flows_by_ccy) | set(realized_pnl_by_ccy)
    )
    # token 实际上必非空：get_current_user 依赖已保证 Bearer header 合法，否则先行 401；
    # 这里 token=None 分支是防御性的（理论不可达），保留以防未来调用方绕过 auth。
    token = (
        authorization.removeprefix("Bearer ").strip()
        if authorization and authorization.startswith("Bearer ")
        else None
    )
    data_client = (
        DataClient(settings.data_service_url, token)
        if token and (fx_needs_network(all_ccys, base_currency) or has_open_position)
        else None
    )
    # try 前初始化，确保即便 convert() 抛非预期异常也不会在 return 处 NameError（CR）
    fx_warnings: list[str] = []
    try:
        converter = BaseCurrencyConverter(base_currency, data_client)

        # 持仓 mark-to-market 估值,按计价货币分桶。mark 拿不到时 spot 用 avg 兜底
        # （perp 下 (avg−avg)×qty = 0 恰为"未实现盈亏按 0 计"），并记 warning。
        pos_value_by_ccy: dict[str, Decimal] = {}
        valuation_warnings: list[str] = []
        for p in pos_rows:
            qty = Decimal(p["quantity"])
            if qty == 0:
                continue
            ccy = p.get("currency") or resolve_currency(
                p["venue"], p["symbol"], default=base_currency
            )
            avg = Decimal(p["avg_open_price"])
            # perp 行:强平价非空或占用保证金非 0（spot 恒 NULL/0）
            is_perp = p.get("liquidation_price") is not None or (
                Decimal(str(p.get("margin_used") or 0)) != 0
            )
            mark: Decimal | None = None
            if data_client is not None:
                try:
                    ticker = await data_client.get_ticker(
                        venue=p["venue"], symbol=p["symbol"], fresh=False
                    )
                    mark = Decimal(str(ticker["price"]))
                    if ticker.get("is_stale"):
                        valuation_warnings.append(
                            f"{p['venue']}/{p['symbol']} 最新价偏旧"
                            f"（{ticker.get('stale_seconds')}s 前），估值可能不准"
                        )
                except Exception:
                    mark = None
            if mark is None:
                valuation_warnings.append(
                    f"{p['venue']}/{p['symbol']} 最新价不可用，"
                    + ("perp 未实现盈亏按 0 计" if is_perp else "按开仓均价估值")
                )
                mark = avg
            value = (mark - avg) * qty if is_perp else qty * mark
            pos_value_by_ccy[ccy] = pos_value_by_ccy.get(ccy, Decimal(0)) + value

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
        # 净外生入金折算(同一 converter,汇率已缓存)
        net_flows_base = Decimal(0)
        for cur, amt in flows_by_ccy.items():
            converted = await converter.convert(amt, cur)
            if converted is not None:
                net_flows_base += converted
        fx_warnings = converter.warnings + valuation_warnings
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
        net_external_flows=float(net_flows_base),
        fx_warnings=fx_warnings,
        created_at=acct["created_at"],
        updated_at=acct["updated_at"],
    )


@router.post("/accounts/me/deposit", response_model=CashFlowRecord)
async def deposit_to_my_account(
    req: DepositRequest,
    db: DBConn,
    user: Annotated[User, Depends(get_current_user)],
) -> CashFlowRecord:
    """给当前账户充值(外生资金事件):流水行 + 余额更新同事务,先留痕再改钱。

    充值不改 ``initial_cash``(充值 ≠ 赚钱;真实绩效口径由流水可还原)。
    """
    account_id = account_id_from_user(user)
    async with db.transaction():
        # 锁账户行:与购买力守门/并发充值串行化(读余额 → 变更 → 记流水)
        acct = await accounts_store.get_or_create(db, account_id, for_update=True)
        currency = (req.currency or acct["base_currency"]).strip().upper()
        # 白名单:任意字符串会建出 FX 永远折算不了的垃圾桶(常驻 fx_warnings 且删不掉)
        if currency not in KNOWN_CASH_CURRENCIES:
            raise InalphaError(
                f"unsupported deposit currency {currency!r}; "
                f"supported: {', '.join(sorted(KNOWN_CASH_CURRENCIES))}",
                code="UNSUPPORTED_CURRENCY",
                status_code=422,
            )
        amount = Decimal(str(req.amount))
        new_balance = await accounts_store.apply_cash_delta(
            db, account_id, amount, currency=currency
        )
        flow = await accounts_store.record_cash_flow(
            db, account_id, kind="deposit", currency=currency,
            amount=amount, balance_after=new_balance, note=req.note,
        )
    return _row_to_cash_flow(flow)


@router.post("/accounts/me/reset", response_model=CashFlowRecord)
async def reset_my_account(
    req: ResetAccountRequest,
    db: DBConn,
    user: Annotated[User, Depends(get_current_user)],
) -> CashFlowRecord:
    """重置当前账户到初始状态:删全部持仓行 + 现金回到 ``{base: initial_cash}``。

    - **有 running run 时 409**(否则 runner 下一根 bar 又把仓开回来);
    - orders / closed_trades / strategy_runs 历史全部保留(审计不可抹),重置后
      绩效从新基准起算(``initial_cash`` 更新为本轮值);
    - 流水 kind=reset:amount = 新初始额 − 旧折算总现金(本地汇率近似),note 记
      旧桶明细与清仓行数,审计可还原。
    """
    account_id = account_id_from_user(user)
    async with db.transaction():
        acct = await accounts_store.get_or_create(db, account_id, for_update=True)
        running = await runs_store.count_running_by_account(db, account_id)
        if running > 0:
            raise AccountHasRunningRunsError(
                f"account has {running} running strategy_runs; stop them before reset "
                "(a running runner would immediately re-open positions)",
                details={"running": running},
            )
        base_ccy = acct["base_currency"]
        new_initial = (
            Decimal(str(req.initial_cash))
            if req.initial_cash is not None
            else Decimal(str(acct["initial_cash"]))
        )
        old_balances = {
            cur: Decimal(str(amt))
            for cur, amt in (acct.get("cash_balances") or {}).items()
        }
        # 旧总额只做流水 amount 的近似口径(本地汇率,拿不到的桶排除);精确旧桶
        # 明细原样进 note,审计不失真。
        converter = BaseCurrencyConverter(base_ccy, None)
        old_total = await convert_cash_balances(converter, old_balances)
        deleted = await positions_store.delete_by_account(db, account_id)
        await accounts_store.reset_cash_balances(
            db, account_id, initial_cash=new_initial, base_currency=base_ccy
        )
        note_auto = (
            f"reset: 清持仓 {deleted} 行; 旧现金桶 "
            + ", ".join(f"{c}={a}" for c, a in sorted(old_balances.items()))
            + f"; 新基准 {new_initial} {base_ccy}"
        )
        flow = await accounts_store.record_cash_flow(
            db, account_id, kind="reset", currency=base_ccy,
            amount=new_initial - old_total, balance_after=new_initial,
            note=f"{note_auto}; {req.note}" if req.note else note_auto,
        )
    return _row_to_cash_flow(flow)


@router.get("/accounts/me/cash_flows", response_model=list[CashFlowRecord])
async def list_my_cash_flows(
    db: DBConn,
    user: Annotated[User, Depends(get_current_user)],
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> list[CashFlowRecord]:
    """列当前账户外生资金流水(充值/提取/重置),最近的在前。"""
    account_id = account_id_from_user(user)
    rows = await accounts_store.list_cash_flows(db, account_id, limit=limit)
    return [_row_to_cash_flow(r) for r in rows]


# ────────────────────────────────────────────────────────────────────
# 内部辅助
# ────────────────────────────────────────────────────────────────────


def _row_to_cash_flow(row: dict[str, Any]) -> CashFlowRecord:
    return CashFlowRecord(
        id=row["id"],
        kind=row["kind"],
        currency=row["currency"],
        amount=float(row["amount"]),
        balance_after=float(row["balance_after"]),
        note=row.get("note"),
        created_at=row["created_at"],
    )


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
    for k in ("quantity", "avg_open_price", "realized_pnl", "margin_used", "liquidation_price"):
        if k in out and out[k] is not None:
            out[k] = float(out[k])
    # trading_mode 派生(positions 表无该列):强平价非空或占用保证金非 0 → perp。
    # 让前端显式标注现货/合约,不再靠 liquidation_price 隐式推断。
    out["trading_mode"] = (
        "perp"
        if out.get("liquidation_price") is not None or (out.get("margin_used") or 0) != 0
        else "spot"
    )
    return out
