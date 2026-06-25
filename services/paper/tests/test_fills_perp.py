"""``apply_fill_to_positions_and_cash`` perp 记账集成测试(DB)。

口径:perp 开仓不动名义、只占 IM(写 positions.margin_used/leverage/liquidation_price);
平仓把已实现盈亏入 cash;与内存 Portfolio 同算法。
"""
from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest
from inalpha_shared.db import get_conn

from inalpha_paper.fills import apply_fill_to_positions_and_cash
from inalpha_paper.storage import accounts as accounts_store
from inalpha_paper.storage import positions as positions_store

pytestmark = pytest.mark.integration

_VENUE = "binance"
_SYM = "BTC/USDT:USDT"
_TS = datetime(2026, 6, 25, 4, 0, tzinfo=UTC)


async def _fund_usdt(conn: Any, account_id: Any, amount: str) -> None:
    await accounts_store.get_or_create(conn, account_id)
    await accounts_store.apply_cash_delta(conn, account_id, Decimal(amount), currency="USDT")


async def test_perp_open_short_books_margin_not_notional(app_with_lifespan: Any) -> None:
    account_id = uuid4()
    async with get_conn() as conn, conn.transaction():
        await _fund_usdt(conn, account_id, "10000")
        realized = await apply_fill_to_positions_and_cash(
            conn, account_id=account_id, venue=_VENUE, symbol=_SYM, side="SELL",
            quantity=Decimal("1"), fill_price=Decimal("100"), fee=Decimal("0"),
            ts_event=_TS, order_id="o-short", trading_mode="perp", leverage=10,
        )
    assert realized == Decimal(0)  # 开仓无已实现盈亏
    async with get_conn() as conn:
        pos = await positions_store.get(conn, account_id=account_id, venue=_VENUE, symbol=_SYM)
        acct = await accounts_store.get(conn, account_id)
    assert Decimal(str(pos["quantity"])) == Decimal("-1")  # 裸空开出(perp 合法)
    assert Decimal(str(pos["margin_used"])) == Decimal("10")  # IM = 1×100/10
    assert int(pos["leverage"]) == 10
    assert pos["liquidation_price"] is not None
    # 开仓不动名义、fee=0 → USDT 桶仍 10000
    assert Decimal(str(acct["cash_balances"]["USDT"])) == Decimal("10000")


async def test_perp_short_close_realizes_pnl_into_cash(app_with_lifespan: Any) -> None:
    account_id = uuid4()
    async with get_conn() as conn, conn.transaction():
        await _fund_usdt(conn, account_id, "10000")
        # 开空 1@100
        await apply_fill_to_positions_and_cash(
            conn, account_id=account_id, venue=_VENUE, symbol=_SYM, side="SELL",
            quantity=Decimal("1"), fill_price=Decimal("100"), fee=Decimal("0"),
            ts_event=_TS, order_id="o-open", trading_mode="perp", leverage=10,
        )
        # 平空:BUY 1@90 → 做空赚 (100-90)×1 = +10
        realized = await apply_fill_to_positions_and_cash(
            conn, account_id=account_id, venue=_VENUE, symbol=_SYM, side="BUY",
            quantity=Decimal("1"), fill_price=Decimal("90"), fee=Decimal("0"),
            ts_event=_TS, order_id="o-close", trading_mode="perp", leverage=10,
        )
    assert realized == Decimal("10")  # 平仓实现 +10
    async with get_conn() as conn:
        pos = await positions_store.get(conn, account_id=account_id, venue=_VENUE, symbol=_SYM)
        acct = await accounts_store.get(conn, account_id)
    assert Decimal(str(pos["quantity"])) == Decimal("0")  # 已平
    assert Decimal(str(pos["margin_used"])) == Decimal("0")  # 保证金释放
    assert pos["liquidation_price"] is None
    # 实现盈亏 +10 进 USDT 桶
    assert Decimal(str(acct["cash_balances"]["USDT"])) == Decimal("10010")


async def test_spot_fill_unchanged(app_with_lifespan: Any) -> None:
    """spot 默认口径零回归:BUY 动名义(cash 减 notional)。"""
    account_id = uuid4()
    async with get_conn() as conn, conn.transaction():
        await _fund_usdt(conn, account_id, "10000")
        await apply_fill_to_positions_and_cash(
            conn, account_id=account_id, venue=_VENUE, symbol="BTC/USDT", side="BUY",
            quantity=Decimal("1"), fill_price=Decimal("100"), fee=Decimal("0"),
            ts_event=_TS, order_id="o-spot",  # 默认 trading_mode=spot
        )
    async with get_conn() as conn:
        acct = await accounts_store.get(conn, account_id)
        pos = await positions_store.get(conn, account_id=account_id, venue=_VENUE, symbol="BTC/USDT")
    assert Decimal(str(acct["cash_balances"]["USDT"])) == Decimal("9900")  # 现货买:cash -= notional
    assert Decimal(str(pos["margin_used"])) == Decimal("0")  # spot 不占保证金
