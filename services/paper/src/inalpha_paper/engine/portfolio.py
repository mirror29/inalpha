"""``Portfolio`` —— 持仓 + 现金 + mark-to-market 估值。

订阅 ``events.fills.*`` 自动维护 ``Position`` + cash；订阅完后发出
``events.position.<strategy_id>`` 给 Strategy 消费。

设计简化（MVP）：

- 现金账户单一（不区分 base / quote currency；统一记 quote）
- 手续费比例固定（构造时传入），从现金扣
- 不模拟 margin / 保证金 / 杠杆（D-7+ 接合约时再加）
"""
from __future__ import annotations

from ..kernel.identifiers import InstrumentId
from ..kernel.msgbus import MessageBus
from ..model.events import OrderFilled, PositionChanged, PositionClosed, PositionOpened
from ..model.orders import OrderSide
from ..model.positions import Position


class Portfolio:
    """单账户 portfolio。"""

    def __init__(
        self,
        msgbus: MessageBus,
        initial_cash: float = 10_000.0,
        fee_rate: float = 0.001,  # 0.1% 默认（Binance taker 量级）
    ) -> None:
        if initial_cash <= 0:
            raise ValueError(f"initial_cash must be positive, got {initial_cash}")
        if not 0 <= fee_rate < 1:
            raise ValueError(f"fee_rate must be in [0, 1), got {fee_rate}")

        self._msgbus = msgbus
        self._initial_cash = initial_cash
        self._cash = initial_cash
        self._fee_rate = fee_rate
        self._positions: dict[InstrumentId, Position] = {}
        # 最新 mark 价（每根 bar 推进时更新），用于 unrealized PnL
        self._marks: dict[InstrumentId, float] = {}
        # 累计手续费、累计成交笔数
        self._total_fees: float = 0.0
        self._trade_count: int = 0
        # equity curve: list of (ts_ns, equity)；BacktestEngine 每根 bar 调 snapshot() 追加
        self._equity_curve: list[tuple[int, float]] = []
        # round-trip 单笔盈亏（仅在 position 完全平仓时记一笔），用于胜率
        self._closed_trade_pnls: list[float] = []
        # 上一次 close 时 position.realized_pnl 的快照，便于算单笔增量
        self._last_realized_pnl: dict[InstrumentId, float] = {}

        # 订阅所有 fill（通配）
        self._msgbus.subscribe("events.fills.*", self._handle_fill)

    # ─── 状态查询 ───

    @property
    def initial_cash(self) -> float:
        return self._initial_cash

    @property
    def cash(self) -> float:
        return self._cash

    @property
    def total_fees(self) -> float:
        return self._total_fees

    @property
    def trade_count(self) -> int:
        return self._trade_count

    def position(self, instrument_id: InstrumentId) -> Position | None:
        return self._positions.get(instrument_id)

    def positions(self) -> dict[InstrumentId, Position]:
        return dict(self._positions)

    def update_mark(self, instrument_id: InstrumentId, mark_price: float) -> None:
        """BacktestEngine 每根 bar 调一次，更新 mark-to-market 估值用的最新价。"""
        self._marks[instrument_id] = mark_price

    def equity(self) -> float:
        """总权益 = cash + 所有持仓的 mark-to-market 价值。

        持仓估值约定：用最新 mark price 计算 ``quantity * mark``；
        没 mark 的（极少见，bar 还没来过）用 ``avg_open_price`` 兜底。
        """
        market_value = 0.0
        for inst, pos in self._positions.items():
            if pos.is_flat:
                continue
            mark = self._marks.get(inst, pos.avg_open_price)
            market_value += pos.quantity * mark
        return self._cash + market_value

    def total_return_pct(self) -> float:
        return (self.equity() - self._initial_cash) / self._initial_cash * 100.0

    @property
    def equity_curve(self) -> list[tuple[int, float]]:
        """(ts_ns, equity) 序列；BacktestEngine 每根 bar 追加一个点。"""
        return list(self._equity_curve)

    @property
    def closed_trade_pnls(self) -> list[float]:
        """每次完整平仓记一笔的 round-trip 盈亏（已扣手续费？否，**仅价差盈亏**）。

        实现注：用 ``Position.realized_pnl`` 的增量。手续费在 ``_handle_fill`` 单独累加进
        ``total_fees``，不进 round-trip pnl —— 这样净收益与持仓 PnL 解耦，胜率更纯粹。
        """
        return list(self._closed_trade_pnls)

    def snapshot(self, ts_ns: int) -> None:
        """记录当前 equity 到曲线。BacktestEngine 每根 bar 调一次。

        实现注：同一个 ts_ns 重复调以最后一次为准（用于 bar close 那个点最终更新）。
        """
        eq = self.equity()
        if self._equity_curve and self._equity_curve[-1][0] == ts_ns:
            self._equity_curve[-1] = (ts_ns, eq)
        else:
            self._equity_curve.append((ts_ns, eq))

    # ─── 事件处理 ───

    def _handle_fill(self, msg: object) -> None:
        if not isinstance(msg, OrderFilled):
            return
        if msg.instrument_id is None:
            return

        instrument_id = msg.instrument_id
        pos = self._positions.get(instrument_id)
        if pos is None:
            pos = Position(instrument_id=instrument_id)
            self._positions[instrument_id] = pos

        was_flat = pos.is_flat
        pos.apply_fill(msg.side, msg.fill_quantity, msg.fill_price, msg.ts_event)
        now_flat = pos.is_flat

        # 现金 + 手续费
        notional = msg.fill_quantity * msg.fill_price
        fee = notional * self._fee_rate
        if msg.side == OrderSide.BUY:
            self._cash -= notional + fee
        else:
            self._cash += notional - fee
        self._total_fees += fee
        self._trade_count += 1

        # 选择对应的 PositionEvent 类型
        event_cls: type[PositionOpened] | type[PositionChanged] | type[PositionClosed]
        if was_flat and not now_flat:
            event_cls = PositionOpened
            self._last_realized_pnl[instrument_id] = pos.realized_pnl
        elif not was_flat and now_flat:
            event_cls = PositionClosed
            # 单笔 round-trip 盈亏 = 这次 close 后的 realized_pnl 减去开仓时 baseline
            baseline = self._last_realized_pnl.get(instrument_id, 0.0)
            self._closed_trade_pnls.append(pos.realized_pnl - baseline)
            self._last_realized_pnl[instrument_id] = pos.realized_pnl
        else:
            event_cls = PositionChanged

        pos_evt = event_cls(
            instrument_id=instrument_id,
            strategy_id=msg.strategy_id,
            quantity=pos.quantity,
            avg_open_price=pos.avg_open_price,
            realized_pnl=pos.realized_pnl,
            generation=pos.generation,
            ts_event=msg.ts_event,
            ts_init=msg.ts_event,
        )
        self._msgbus.publish(f"events.position.{msg.strategy_id}", pos_evt)
