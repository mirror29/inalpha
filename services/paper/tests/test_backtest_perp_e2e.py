"""perp 做空端到端回测:做空盈利 + 杠杆保证金 + 资金费 + 维持保证金强平。

证明引擎 perp 全生命周期口径正确(回测路径,无 DB / 无外部服务)。
"""
from __future__ import annotations

from uuid import uuid4

from inalpha_paper.engine.backtest import BacktestEngine
from inalpha_paper.kernel.identifiers import ClientOrderId, InstrumentId
from inalpha_paper.model.data import Bar
from inalpha_paper.model.orders import Order, OrderSide, OrderType
from inalpha_paper.strategy.base import Strategy

_H = 3600 * 1_000_000_000  # 1h ns


def _btc() -> InstrumentId:
    return InstrumentId(symbol="BTC/USDT:USDT", venue="binance")


def _bars(closes: list[float]) -> list[Bar]:
    """逐根 hourly bar(OHLC=close,简化),ts 从 epoch 起每小时一根。"""
    out = []
    for i, c in enumerate(closes):
        out.append(Bar(
            instrument_id=_btc(), timeframe="1h",
            open=c, high=c, low=c, close=c, volume=1.0,
            ts_event=i * _H, ts_init=i * _H,
        ))
    return out


class _ShortThenCover(Strategy):
    """首根 bar 开空,close 跌到 cover_at 时买入平空(cover)。"""

    def __init__(self, name, clock, msgbus, instrument_id, timeframe="1h", trade_size=1.0, cover_at=90.0):  # type: ignore[no-untyped-def]
        super().__init__(name, clock, msgbus)
        self._iid = instrument_id
        self._tf = timeframe
        self._sz = trade_size
        self._cover_at = cover_at
        self._is_short = False
        self._open_qty = 0.0
        self._opened = False

    def on_start(self) -> None:
        self.subscribe_bars(self._iid, self._tf)

    def on_bar(self, bar: Bar) -> None:
        if bar.instrument_id != self._iid:
            return
        if not self._opened and not self._is_short:
            self._opened = True
            self._submit(OrderSide.SELL, self._sz)
        elif self._is_short and bar.close <= self._cover_at:
            self._submit(OrderSide.BUY, self._open_qty)

    def on_position_opened(self, event) -> None:  # type: ignore[no-untyped-def]
        self._is_short = event.quantity < 0
        self._open_qty = abs(event.quantity)

    def on_position_closed(self, event) -> None:  # type: ignore[no-untyped-def]
        self._is_short = False
        self._open_qty = 0.0

    def _submit(self, side, qty) -> None:  # type: ignore[no-untyped-def]
        self.submit_order(Order(
            client_order_id=ClientOrderId("st-" + uuid4().hex[:8]),
            instrument_id=self._iid, side=side, type=OrderType.MARKET, quantity=qty,
        ))


class _ShortOnce(Strategy):
    """首根 bar 开空后不动(让框架强平兜底)。"""

    def __init__(self, name, clock, msgbus, instrument_id, timeframe="1h", trade_size=1.0):  # type: ignore[no-untyped-def]
        super().__init__(name, clock, msgbus)
        self._iid = instrument_id
        self._tf = timeframe
        self._sz = trade_size
        self._done = False

    def on_start(self) -> None:
        self.subscribe_bars(self._iid, self._tf)

    def on_bar(self, bar: Bar) -> None:
        if bar.instrument_id == self._iid and not self._done:
            self._done = True
            self.submit_order(Order(
                client_order_id=ClientOrderId("so-" + uuid4().hex[:8]),
                instrument_id=self._iid, side=OrderSide.SELL,
                type=OrderType.MARKET, quantity=self._sz,
            ))


def _run(strat_cls, closes, **engine_kw):  # type: ignore[no-untyped-def]
    eng = BacktestEngine(initial_cash=10_000.0, fee_rate=0.0, **engine_kw)
    strat = strat_cls(name="t", clock=eng.clock, msgbus=eng.msgbus, instrument_id=_btc())
    eng.add_strategy(strat)
    eng.run(_bars(closes))
    return eng


def test_perp_short_profits_and_books_flat() -> None:
    """开空 → 跌 → 平空盈利:末态空仓,equity > 初始(做空价跌则盈)。"""
    # bar0 开空意图 → bar1 撮合开空@100;跌到 ≤90 平空
    eng = _run(_ShortThenCover, [100, 100, 96, 92, 90, 88, 88],
               trading_mode="perp", leverage=10)
    pos = eng.portfolio.position(_btc())
    assert pos is None or pos.is_flat  # 已平
    pnls = eng.portfolio.closed_trade_pnls
    assert len(pnls) == 1 and pnls[0] > 0  # 做空盈利(平在更低价)
    assert eng.portfolio.equity() > 10_000.0  # 杠杆下小本金赚价差


def test_perp_short_funding_received_lifts_equity() -> None:
    """正费率下空头**收**资金费 → 比零费率时末值更高(funding 进现金流)。"""
    closes = [100, 100, 96, 92, 90, 88, 88, 88, 88, 88]  # 跨过 8h 结算点(第 8 根 ts=8h)
    base = _run(_ShortThenCover, closes, trading_mode="perp", leverage=10, funding_rate=0.0)
    funded = _run(_ShortThenCover, closes, trading_mode="perp", leverage=10, funding_rate=0.0001)
    # 注:策略约第 4 根就平空,平仓后无持仓不再计提——为确保持仓期跨结算点,用 ShortOnce 持有到底
    base2 = _run(_ShortOnce, closes, trading_mode="perp", leverage=10, funding_rate=0.0)
    funded2 = _run(_ShortOnce, closes, trading_mode="perp", leverage=10, funding_rate=0.0001)
    assert funded2.portfolio.cash > base2.portfolio.cash  # 空头持有跨结算点,正费率收钱
    # ShortThenCover 早平,funding 影响小或无,只验不报错
    assert funded.portfolio.equity() >= base.portfolio.equity() - 1e-6


def test_perp_long_liquidation_fires() -> None:
    """10x 多头价崩穿含 buffer 强平价 → 框架强平(tag=liquidation),末态平仓。"""

    class _LongOnce(Strategy):
        def __init__(self, name, clock, msgbus, instrument_id, timeframe="1h"):  # type: ignore[no-untyped-def]
            super().__init__(name, clock, msgbus)
            self._iid = instrument_id
            self._tf = timeframe
            self._done = False

        def on_start(self) -> None:
            self.subscribe_bars(self._iid, self._tf)

        def on_bar(self, bar: Bar) -> None:
            if bar.instrument_id == self._iid and not self._done:
                self._done = True
                self.submit_order(Order(
                    client_order_id=ClientOrderId("lo-" + uuid4().hex[:8]),
                    instrument_id=self._iid, side=OrderSide.BUY,
                    type=OrderType.MARKET, quantity=1.0,
                ))

    # 10x 多头 @100 → liq≈90.36,buffer 5% → 触发 ~94.88;价崩到 94 → 强平,下一根撮合
    eng = BacktestEngine(
        initial_cash=10_000.0, fee_rate=0.0, trading_mode="perp", leverage=10,
        protective_stop_loss_pct=0.20,  # 让 guard 被创建(强平独立于此阈值)
    )
    strat = _LongOnce(name="lo", clock=eng.clock, msgbus=eng.msgbus, instrument_id=_btc())
    eng.add_strategy(strat)
    eng.run(_bars([100, 100, 94, 93, 93]))

    pos = eng.portfolio.position(_btc())
    assert pos is None or pos.is_flat  # 已被强平
    liq_fills = [f for f in eng.portfolio.fills if f.tag == "liquidation"]
    assert len(liq_fills) == 1  # 发生了一次强平成交
