"""``Portfolio`` —— 持仓 + 现金 + mark-to-market 估值。

订阅 ``events.fills.*`` 自动维护 ``Position`` + cash；订阅完后发出
``events.position.<strategy_id>`` 给 Strategy 消费。

设计简化（MVP）：

- **单币种 per session**：一次回测 / 一个 live run 是单策略单 symbol（同一计价货币），
  此处 ``_cash`` 单标量是正确的。**跨币种是账户聚合层的事**（一个账户跨多市场累积
  多个持仓 / 多次 run）——D-11 在 storage + ``/accounts/me`` 用按币种 cash 桶 + FX 折算
  处理（见 ``storage/accounts.py`` ``cash_balances`` 与 ``fx.BaseCurrencyConverter``），
  不在本引擎内。
- 手续费比例固定（构造时传入），从现金扣
- 不模拟 margin / 保证金 / 杠杆（D-7+ 接合约时再加）
"""
from __future__ import annotations

from uuid import UUID

from ..execution import perp_margin
from ..kernel.identifiers import InstrumentId
from ..kernel.msgbus import MessageBus
from ..model.events import OrderFilled, PositionChanged, PositionClosed, PositionOpened
from ..model.orders import OrderSide, is_protective_signature
from ..model.positions import Position
from .close_detector import ClosedTradeStaging, detect_close
from .report import FillRecord


class Portfolio:
    """单账户 portfolio。"""

    def __init__(
        self,
        msgbus: MessageBus,
        initial_cash: float = 10_000.0,
        fee_rate: float = 0.001,  # 0.1% 默认（Binance taker 量级）
        *,
        account_id: UUID | None = None,
        trading_mode: str = "spot",
        leverage: int = 1,
    ) -> None:
        """初始化。

        Args:
            account_id: close 检测的账户 ID。提供时 fill 触发 close 写入内存队列
                （drain_closed_trades 拉数据写 DB）；None 时不入队（向后兼容）
            trading_mode: ``"spot"``（默认，现货 long-only）或 ``"perp"``（USDT-M 永续 +
                逐仓 + 单向）。perp 下开/加仓不收付名义、只占保证金，盈亏平仓时实现。
            leverage: 杠杆倍数（perp 用；spot 恒 1）。影响初始保证金 IM=notional/leverage
                与购买力 free×leverage，**不影响维持保证金**（见 perp_margin）。
        """
        if initial_cash <= 0:
            raise ValueError(f"initial_cash must be positive, got {initial_cash}")
        if not 0 <= fee_rate < 1:
            raise ValueError(f"fee_rate must be in [0, 1), got {fee_rate}")
        if trading_mode not in ("spot", "perp"):
            raise ValueError(f"trading_mode must be 'spot' or 'perp', got {trading_mode!r}")

        self._msgbus = msgbus
        self._initial_cash = initial_cash
        self._cash = initial_cash
        self._fee_rate = fee_rate
        self._account_id = account_id
        self._trading_mode = trading_mode
        self._leverage = max(1, int(leverage))
        # perp 已占用保证金（Σ |qty|×avg_open/leverage），每笔成交后从持仓重算；spot 恒 0。
        self._margin_used: float = 0.0
        self._positions: dict[InstrumentId, Position] = {}
        # ADR-0007：detect_close 入队，待 ClosedTradesWriter 异步写 DB
        self._close_trade_queue: list[ClosedTradeStaging] = []
        # 最新 mark 价（每根 bar 推进时更新），用于 unrealized PnL
        self._marks: dict[InstrumentId, float] = {}
        # 累计手续费、累计成交笔数
        self._total_fees: float = 0.0
        self._trade_count: int = 0
        # equity curve: list of (ts_ns, equity)；BacktestEngine 每根 bar 调 snapshot() 追加
        self._equity_curve: list[tuple[int, float]] = []
        # round-trip 单笔盈亏（仅在 position 完全平仓时记一笔），用于胜率
        self._closed_trade_pnls: list[float] = []
        # 逐笔成交快照（含每笔实现盈亏），回测结束塞进 BacktestReport → 落 backtest_trades
        self._fills: list[FillRecord] = []
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

    @property
    def fee_rate(self) -> float:
        """撮合层撮合前算 notional+fee 守门用。"""
        return self._fee_rate

    @property
    def trading_mode(self) -> str:
        return self._trading_mode

    @property
    def leverage(self) -> int:
        return self._leverage

    @property
    def margin_used(self) -> float:
        """perp 已占用保证金;spot 恒 0。"""
        return self._margin_used

    def free_margin(self) -> float:
        """可用保证金 = wallet(cash) − 已占用保证金。spot 下 margin_used=0 → = cash。"""
        return self._cash - self._margin_used

    def buying_power(self) -> float:
        """可用购买力（名义额）：perp = free_margin×leverage;spot = cash。"""
        if self._trading_mode == "perp":
            return self.free_margin() * self._leverage
        return self._cash

    def _recompute_margin_used(self) -> None:
        """从当前持仓重算 perp 已占用保证金(增量不漂移);spot 恒 0。"""
        if self._trading_mode != "perp":
            self._margin_used = 0.0
            return
        total = 0.0
        for pos in self._positions.values():
            if pos.is_flat:
                continue
            total += abs(pos.quantity) * pos.avg_open_price / self._leverage
        self._margin_used = total

    def position(self, instrument_id: InstrumentId) -> Position | None:
        return self._positions.get(instrument_id)

    def positions(self) -> dict[InstrumentId, Position]:
        return dict(self._positions)

    # ─── 撮合前守门（spot:禁透支/禁裸 SHORT · perp:保证金购买力） ───

    def can_afford_buy(
        self, qty: float, price: float, *, instrument_id: InstrumentId | None = None
    ) -> bool:
        """BUY 前守门。

        - spot:现金够不够 ``notional + fee``（避免 cash 变负，旧 BTC -98% bug 同源）。
        - perp:成交后的**目标仓**所需初始保证金 + fee 不得超过 wallet(cash)。统一处理
          开/加/减/平/反手:``prospective_margin = |new_qty|×price/leverage``（用成交价近似
          整仓口径,守门足够保守）。perp 需 ``instrument_id`` 取当前仓。
        """
        if qty <= 0 or price <= 0:
            return False
        fee = qty * price * self._fee_rate
        if self._trading_mode != "perp":
            return self._cash >= qty * price + fee
        cur = self._positions.get(instrument_id) if instrument_id is not None else None
        cur_qty = cur.quantity if cur is not None else 0.0
        cur_im = abs(cur_qty) * (cur.avg_open_price if cur is not None else price) / self._leverage
        prospective_margin = abs(cur_qty + qty) * price / self._leverage
        # 与其他持仓的已占用保证金互斥:可用 = free_margin + 本仓当前 IM(= cash − 其他仓 IM)。
        # 用裸 cash 会让多个 perp 仓各自只比全钱包 → 合计 IM 超钱包、free_margin 变负、回测静默
        # 账务崩坏(跨 symbol 聚合,#114)。单仓时 free_margin+cur_im == cash,行为不变。
        return prospective_margin + fee <= self.free_margin() + cur_im

    def can_afford_sell(
        self, instrument_id: InstrumentId, qty: float, *, price: float | None = None
    ) -> bool:
        """SELL 前守门。

        - spot 禁裸 SHORT:当前 LONG 仓位够不够卖出 ``qty``（``flat`` 下 SELL 必拒）。
        - perp 放开做空:按保证金校验——成交后目标仓所需 IM + fee 不超过 wallet(cash)。
        """
        if qty <= 0:
            return False
        if self._trading_mode != "perp":
            pos = self._positions.get(instrument_id)
            current = pos.quantity if pos is not None else 0.0
            return current >= qty
        if price is None:
            raise ValueError("perp can_afford_sell 需要 price 计算保证金")
        fee = qty * price * self._fee_rate
        cur = self._positions.get(instrument_id)
        cur_qty = cur.quantity if cur is not None else 0.0
        cur_im = abs(cur_qty) * (cur.avg_open_price if cur is not None else price) / self._leverage
        prospective_margin = abs(cur_qty - qty) * price / self._leverage
        # 与其他持仓互斥:可用 = free_margin + 本仓当前 IM(同 can_afford_buy)。单仓时 == cash。
        return prospective_margin + fee <= self.free_margin() + cur_im

    def adjust_cash(self, delta: float) -> None:
        """外生现金调整(不经 fill):live session resume 回灌历史净已实现盈亏用。

        重启后 session 钱包从 initial_cash(allocation)重建,若不把 run 此前的
        已实现盈亏(−手续费)灌回,亏损 run 一重启钱包就回血满额、allocation 花费
        记忆丢失。除本方法与 apply_funding 外,现金只应经 fill 变动。
        """
        self._cash += delta

    def update_mark(self, instrument_id: InstrumentId, mark_price: float) -> None:
        """BacktestEngine 每根 bar 调一次，更新 mark-to-market 估值用的最新价。"""
        self._marks[instrument_id] = mark_price

    def apply_funding(
        self, instrument_id: InstrumentId, funding_rate: float, *, mark: float | None = None
    ) -> float:
        """perp:在结算时点对当前持仓计提资金费,进 **cash 已实现现金流**(不并入 UPNL)。

        ``funding = qty_signed × mark × rate``(正费率多头付出、空头收取);从 cash 扣该
        支付额(负支付 = 入账)。spot 或 flat → no-op 返 0。调用方(回测 / live bar 循环)按
        :func:`perp_margin.funding_settlements_between` 的结算次数决定调几次。返回本次支付额。
        """
        if self._trading_mode != "perp":
            return 0.0
        pos = self._positions.get(instrument_id)
        if pos is None or pos.is_flat:
            return 0.0
        m = mark if mark is not None else self._marks.get(instrument_id, pos.avg_open_price)
        payment = perp_margin.funding_payment(
            qty_signed=pos.quantity, mark_price=m, funding_rate=funding_rate
        )
        self._cash -= payment  # 正支付 = 扣钱包;负 = 入账
        return payment

    def equity(self) -> float:
        """总权益。

        - spot:``cash + Σ quantity×mark``（持仓 mark-to-market 市值）。
        - perp:``cash(wallet) + Σ 未实现盈亏``;开仓不动名义,cash 即钱包余额,
          未实现盈亏 = ``(mark − avg_open)×quantity``(带符号,做空价跌则盈)。

        没 mark 的（极少见，bar 还没来过）用 ``avg_open_price`` 兜底。
        """
        if self._trading_mode == "perp":
            upnl = 0.0
            for inst, pos in self._positions.items():
                if pos.is_flat:
                    continue
                mark = self._marks.get(inst, pos.avg_open_price)
                upnl += (mark - pos.avg_open_price) * pos.quantity
            return self._cash + upnl
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

    def drain_closed_trades(self) -> list[ClosedTradeStaging]:
        """ADR-0007：拉走 close 队列。`ClosedTradesWriter` 周期调，写 DB 成功后丢弃返回的列表。

        幂等：再次调返空 list（队列已被清空）。**只在 ``account_id`` 提供时有数据**。
        """
        out = list(self._close_trade_queue)
        self._close_trade_queue.clear()
        return out

    @property
    def fills(self) -> list[FillRecord]:
        """逐笔成交快照（含每笔实现盈亏）；`BacktestEngine` 结束时塞进 report。"""
        return list(self._fills)

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
        prev_qty = pos.quantity  # apply_fill 前的方向，用于 flip 检测
        prev_avg = pos.avg_open_price  # perp 破产 clamp 用（成交前持仓 IM 基准）
        prev_realized = pos.realized_pnl  # 算本笔实现盈亏增量用（apply_fill 后会变）

        # ADR-0007：detect close **必须**在 apply_fill 之前，否则 prev_position 已变
        if self._account_id is not None:
            staging = detect_close(
                pos, msg,
                account_id=self._account_id,
                order_tag=msg.tag,
            )
            if staging is not None:
                self._close_trade_queue.append(staging)

        pos.apply_fill(
            msg.side, msg.fill_quantity, msg.fill_price, msg.ts_event,
            open_order_id=str(msg.client_order_id),
        )
        now_flat = pos.is_flat
        new_qty = pos.quantity

        # 反向开仓（flip）：prev 与 new 都非零、方向相反；逻辑上等价于"先平掉旧 leg，
        # 再用剩余 quantity 开新 leg"。Position.apply_fill 已经把被平那部分的 PnL
        # 累计到 pos.realized_pnl 里（model/positions.py:82-90），这里只需 round-trip
        # 入账 + 更新 baseline。**不 detect 的话 win_rate / round-trip 计数会长期错算**。
        flipped = (
            not was_flat
            and not now_flat
            and (prev_qty > 0) != (new_qty > 0)
        )

        # 现金 + 手续费
        notional = msg.fill_quantity * msg.fill_price
        fee = notional * self._fee_rate
        if self._trading_mode == "perp":
            # 永续:开/加仓不收付名义、只占保证金;cash 只随**已实现盈亏**(平/减/反手那部分)
            # 与手续费变动。realized 增量 = apply_fill 后 pos.realized_pnl − 成交前快照。
            realized_increment = pos.realized_pnl - prev_realized
            # 逐仓破产 clamp:平/减仓的**亏损不超过被平那部分的开仓保证金**(bar 跳空穿强平价
            # 的兜底;超出部分由保险基金吸收,钱包不再扣)。按**被平量**算 IM 而非全仓——部分平仓
            # 只清被平那部分的逐仓保证金,剩余仓的保证金仍独立担保;用全仓 IM 会把剩余仓保证金
            # 也一并耗光(CR)。反手时被平量 = |prev_qty|(新 leg 尚无已实现)。
            closed_qty = min(abs(msg.fill_quantity), abs(prev_qty))
            margin_before = closed_qty * prev_avg / self._leverage
            if realized_increment < 0 and -realized_increment > margin_before > 0:
                realized_increment = -margin_before
            self._cash += realized_increment - fee
            # 强平罚金:tag=liquidation 时按名义额外扣(惩罚"靠强平兜底",与回测/live 同口径)
            if msg.tag == "liquidation":
                penalty = notional * perp_margin.DEFAULT_LIQUIDATION_PENALTY_RATE
                self._cash -= penalty
                self._total_fees += penalty
            self._recompute_margin_used()
        elif msg.side == OrderSide.BUY:
            self._cash -= notional + fee
        else:
            self._cash += notional - fee
        self._total_fees += fee
        self._trade_count += 1

        # 逐笔成交快照：每笔实现盈亏 = 本笔引起的 realized_pnl 增量（开仓笔=0，平仓/反手笔
        # 为价差盈亏，不含手续费）；intent 按成交前持仓方向 + side 判（同 live_runner._intent_for）。
        # bar_close 撮合早于本根 bar mark 更新 → 用最新 mark（上一根 close），缺失退回 fill_price。
        if msg.side == OrderSide.BUY:
            intent = "close" if prev_qty < 0 else "open_long"
        else:
            intent = "close" if prev_qty > 0 else "open_short"
        self._fills.append(
            FillRecord(
                ts_ns=msg.ts_event,
                bar_close=self._marks.get(instrument_id, msg.fill_price),
                side=msg.side.value,
                quantity=msg.fill_quantity,
                order_type="MARKET",
                fill_price=msg.fill_price,
                fee=fee,
                realized_pnl=pos.realized_pnl - prev_realized,
                intent=intent,
                tag=msg.tag,
                # 三因子判定是否框架 guard 兜底出场（区分策略自带 stop_loss tag，CR #88）
                is_guard=is_protective_signature(
                    msg.side, msg.tag, msg.client_order_id
                ),
            )
        )

        # 选择对应的 PositionEvent 类型 + round-trip 入账
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
            if flipped:
                # 旧 leg 被平掉的 PnL 入账，baseline 重置到新 leg 起点
                baseline = self._last_realized_pnl.get(instrument_id, 0.0)
                self._closed_trade_pnls.append(pos.realized_pnl - baseline)
                self._last_realized_pnl[instrument_id] = pos.realized_pnl

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
