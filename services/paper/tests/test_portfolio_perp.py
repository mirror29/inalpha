"""Portfolio perp(USDT-M 永续 + 逐仓)记账单测 —— 内存,无 DB。

口径(对齐设计稿):开/加仓不收付名义、只占 IM;盈亏平仓时实现进 cash;equity = cash + UPNL;
做空合法(按保证金购买力校验);spot 默认行为零回归。
"""
from __future__ import annotations

from inalpha_paper.engine.portfolio import Portfolio
from inalpha_paper.kernel.identifiers import InstrumentId
from inalpha_paper.kernel.msgbus import MessageBus
from inalpha_paper.model.events import OrderFilled
from inalpha_paper.model.orders import OrderSide


def _btc() -> InstrumentId:
    return InstrumentId(symbol="BTC/USDT:USDT", venue="binance")


def _fill(
    side: OrderSide, qty: float, price: float, ts: int = 1_000, tag: str | None = None
) -> OrderFilled:
    return OrderFilled(
        client_order_id=f"cid-{ts}",  # type: ignore[arg-type]
        venue_order_id=None,
        instrument_id=_btc(),
        strategy_id="test-strat",  # type: ignore[arg-type]
        side=side,
        fill_quantity=qty,
        fill_price=price,
        ts_event=ts,
        ts_init=ts,
        tag=tag,
    )


def _perp(leverage: int = 10, initial_cash: float = 10_000.0, fee_rate: float = 0.0) -> Portfolio:
    return Portfolio(
        MessageBus(), initial_cash=initial_cash, fee_rate=fee_rate,
        trading_mode="perp", leverage=leverage,
    )


# ─── 开仓:不动名义,只占保证金 ───


def test_perp_open_long_reserves_margin_not_notional() -> None:
    p = _perp(leverage=10)
    p._handle_fill(_fill(OrderSide.BUY, qty=1.0, price=100.0))
    assert p.cash == 100_00.0 - 0.0  # fee=0,开仓不动名义 → cash 不变
    assert p.margin_used == 10.0  # IM = 1×100/10
    assert p.free_margin() == 9_990.0
    assert p.buying_power() == 9_990.0 * 10  # free×leverage
    # mark 未动 → UPNL=0 → equity 仍 = cash
    assert p.equity() == 10_000.0


def test_perp_open_long_only_fee_leaves_cash() -> None:
    p = _perp(leverage=10, fee_rate=0.001)
    p._handle_fill(_fill(OrderSide.BUY, qty=1.0, price=100.0))
    # 只扣 fee=0.1,不动名义
    assert abs(p.cash - (10_000.0 - 0.1)) < 1e-9
    assert p.margin_used == 10.0


# ─── 做空合法 + UPNL(价跌则盈) ───


def test_perp_naked_short_opens_and_profits_on_drop() -> None:
    p = _perp(leverage=10)
    inst = _btc()
    # 空仓直接 SELL → 开空(perp 合法)
    p._handle_fill(_fill(OrderSide.SELL, qty=1.0, price=100.0))
    assert p.position(inst).quantity == -1.0
    assert p.cash == 10_000.0  # 开仓不动名义
    assert p.margin_used == 10.0
    # mark 跌到 90 → 空头 UPNL = (90-100)×(-1) = +10
    p.update_mark(inst, 90.0)
    assert p.equity() == 10_010.0
    # 平空:BUY 1@90 → 实现 +10 进 cash
    p._handle_fill(_fill(OrderSide.BUY, qty=1.0, price=90.0, ts=2_000))
    assert p.position(inst).is_flat
    assert abs(p.cash - 10_010.0) < 1e-9
    assert p.margin_used == 0.0


def test_perp_long_realized_pnl_into_cash_on_close() -> None:
    p = _perp(leverage=10)
    inst = _btc()
    p._handle_fill(_fill(OrderSide.BUY, qty=1.0, price=100.0))
    p.update_mark(inst, 120.0)
    assert p.equity() == 10_020.0  # UPNL +20
    p._handle_fill(_fill(OrderSide.SELL, qty=1.0, price=120.0, ts=2_000))
    assert abs(p.cash - 10_020.0) < 1e-9  # 实现 +20 进 cash
    assert p.margin_used == 0.0


# ─── 保证金购买力守门 ───


def test_perp_can_afford_sell_allows_short_within_margin() -> None:
    p = _perp(leverage=10)
    inst = _btc()
    assert p.can_afford_sell(inst, 1.0, price=100.0) is True  # 裸空合法,IM=10≤cash


def test_perp_can_afford_sell_rejects_short_exceeding_margin() -> None:
    p = _perp(leverage=1, initial_cash=10_000.0)
    inst = _btc()
    # SELL 200@100 → 目标仓 IM = 200×100/1 = 20000 > cash 10000 → 拒
    assert p.can_afford_sell(inst, 200.0, price=100.0) is False


def test_perp_can_afford_buy_margin_aware() -> None:
    p = _perp(leverage=10, initial_cash=1_000.0)
    inst = _btc()
    assert p.can_afford_buy(1.0, 100.0, instrument_id=inst) is True  # IM=10≤1000
    assert p.can_afford_buy(200.0, 100.0, instrument_id=inst) is False  # IM=2000>1000


def test_perp_close_does_not_need_fresh_margin() -> None:
    # 持空后平空(BUY 减仓)→ 目标仓更小 → 恒可负担
    p = _perp(leverage=2, initial_cash=10_000.0)
    inst = _btc()
    p._handle_fill(_fill(OrderSide.SELL, qty=100.0, price=100.0))  # 开空,IM=5000
    assert p.can_afford_buy(100.0, 100.0, instrument_id=inst) is True  # 平空 → 目标仓 0


# ─── spot 零回归 ───


def test_spot_default_unchanged() -> None:
    p = Portfolio(MessageBus(), initial_cash=10_000.0, fee_rate=0.0)  # 默认 spot
    assert p.trading_mode == "spot"
    assert p.margin_used == 0.0
    assert p.buying_power() == 10_000.0
    inst = _btc()
    # spot 禁裸空
    assert p.can_afford_sell(inst, 1.0) is False
    # spot BUY 动名义
    p._handle_fill(_fill(OrderSide.BUY, qty=1.0, price=100.0))
    assert p.cash == 9_900.0  # 现货买:cash -= notional
    assert p.margin_used == 0.0


def test_spot_can_afford_sell_allows_closing_long() -> None:
    p = Portfolio(MessageBus(), initial_cash=10_000.0, fee_rate=0.0)
    inst = _btc()
    p._handle_fill(_fill(OrderSide.BUY, qty=1.0, price=100.0))
    assert p.can_afford_sell(inst, 1.0) is True  # 等量平多放行
    assert p.can_afford_sell(inst, 2.0) is False  # 超卖翻空拒


# ─── 资金费计提(进 cash 现金流,不污染 UPNL) ───


def test_apply_funding_long_pays_on_positive_rate() -> None:
    p = _perp(leverage=10)
    inst = _btc()
    p._handle_fill(_fill(OrderSide.BUY, qty=1.0, price=100.0))  # 多 1@100,cash=10000
    p.update_mark(inst, 100.0)
    pay = p.apply_funding(inst, 0.0001)  # 正费率 → 多头付 1×100×0.0001=0.01
    assert pay == 0.01
    assert p.cash == 10_000.0 - 0.01


def test_apply_funding_short_receives_on_positive_rate() -> None:
    p = _perp(leverage=10)
    inst = _btc()
    p._handle_fill(_fill(OrderSide.SELL, qty=1.0, price=100.0))  # 空
    p.update_mark(inst, 100.0)
    pay = p.apply_funding(inst, 0.0001)  # 空头收 → 负支付 = 入账
    assert pay == -0.01
    assert p.cash == 10_000.0 + 0.01


def test_apply_funding_spot_is_noop() -> None:
    p = Portfolio(MessageBus(), initial_cash=10_000.0, fee_rate=0.0)  # spot
    inst = _btc()
    p._handle_fill(_fill(OrderSide.BUY, qty=1.0, price=100.0))
    assert p.apply_funding(inst, 0.001) == 0.0
    assert p.cash == 9_900.0  # 不被 funding 改


def test_apply_funding_flat_is_noop() -> None:
    p = _perp(leverage=10)
    assert p.apply_funding(_btc(), 0.001) == 0.0


# ─── 引擎 perp 透传(回测==实盘:同一 Portfolio 配置) ───


def test_backtest_engine_perp_portfolio() -> None:
    from inalpha_paper.engine.backtest import BacktestEngine

    eng = BacktestEngine(initial_cash=10_000.0, fee_rate=0.0, trading_mode="perp", leverage=5)
    assert eng.portfolio.trading_mode == "perp"
    assert eng.portfolio.leverage == 5


# ─── 强平罚金 + 逐仓破产 clamp ───


def test_perp_liquidation_penalty_charged() -> None:
    """tag=liquidation 平仓:按名义额外扣罚金(1%);平价无盈亏 → cash 仅减罚金。"""
    p = _perp(leverage=10)  # fee_rate=0
    p._handle_fill(_fill(OrderSide.BUY, qty=1.0, price=100.0))  # 开多 IM=10
    p._handle_fill(_fill(OrderSide.SELL, qty=1.0, price=100.0, ts=2_000, tag="liquidation"))
    # 平价 realized=0,fee=0,penalty = 100×1% = 1.0
    assert p.cash == 10_000.0 - 1.0


def test_perp_isolated_bankruptcy_clamp() -> None:
    """gap 平仓亏损超保证金 → 逐仓只亏该仓保证金(其余由保险基金吸收)。"""
    p = _perp(leverage=10)  # fee_rate=0
    p._handle_fill(_fill(OrderSide.BUY, qty=1.0, price=100.0))  # 多 1@100,IM=10
    # 价崩到 50 平仓:gross 亏 -50,但逐仓最多亏保证金 10
    p._handle_fill(_fill(OrderSide.SELL, qty=1.0, price=50.0, ts=2_000))
    assert p.cash == 10_000.0 - 10.0  # 亏损 clamp 到 10
    assert p.position(_btc()).is_flat


def test_perp_partial_close_clamp_uses_closed_qty_im() -> None:
    """部分平仓的破产 clamp 按**被平量**算 IM,不耗尽剩余仓保证金(CR)。"""
    p = _perp(leverage=5)  # fee_rate=0
    p._handle_fill(_fill(OrderSide.SELL, qty=2.0, price=100.0))  # 空 2@100,整仓 IM=40,每手 20
    # 价跳到 160 平 1 手:gross 亏 -(160-100)*1 = -60,但被平 1 手 IM=20 → clamp 到 -20
    p._handle_fill(_fill(OrderSide.BUY, qty=1.0, price=160.0, ts=2_000))
    assert p.cash == 10_000.0 - 20.0  # 只亏被平那手的 IM 20(旧码用全仓 IM 40 会多扣)
    assert p.position(_btc()).quantity == -1.0  # 剩 1 手空


def test_perp_can_afford_uses_free_margin_across_symbols() -> None:
    """多 symbol perp:can_afford 按 free_margin(扣其他仓 IM)判,不只比全钱包(CR · #114 backtest)。"""
    eth = InstrumentId(symbol="ETH/USDT:USDT", venue="binance")
    p = _perp(leverage=10, initial_cash=30_000.0)  # fee_rate=0
    # 开 BTC:IM = 4×50000/10 = 20000 → 占用后 free_margin 余 10000
    assert p.can_afford_buy(4.0, 50_000.0, instrument_id=_btc())
    p._handle_fill(_fill(OrderSide.BUY, qty=4.0, price=50_000.0))
    assert p.margin_used == 20_000.0
    assert p.free_margin() == 10_000.0
    # 再开 ETH:IM = 50×3000/10 = 15000 > free_margin 10000 → 必拒(旧码比全钱包 30000 会误过)
    assert not p.can_afford_buy(50.0, 3_000.0, instrument_id=eth)
    # IM = 30×3000/10 = 9000 ≤ 10000 → 放行
    assert p.can_afford_buy(30.0, 3_000.0, instrument_id=eth)
