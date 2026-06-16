"""``LiveEngineSession`` —— 复用回测内核、逐根 bar 驱动、拦截下单转外部 plan/exec（D-11）。

与 ``BacktestEngine`` 的差异：

- **逐根 bar 外部驱动**：``feed_bar(bar)`` 替代 ``run(bars)`` 批跑——live runner 每拉到
  一根新 bar 喂一次。
- **不自动撮合**：用 ``_CaptureGateway`` 替代 ``SimulatedExchange``——策略下单经
  ExecutionEngine 后只被收集，**不在本进程撮合**；撮合 + 落账由 live runner 走护栏内
  plan/exec 链路（保住 DB-backed RiskGuard + 一次性 token + 审计）。
- **风控旁路**：``RISK_ENGINE_ENDPOINT`` 注册一个 pass-through（直接转
  ExecutionEngine），不跑内存 RiskEngine 规则——风控统一由外部 ``risk_guard.enforce``
  （per-account DB 规则）做，避免双重风控语义。

**为什么复用 ExecutionEngine 而不手工合成事件**：成交后 ``confirm_fill`` 发
``internal.venue.filled`` 走 ExecutionEngine 原生 ``_handle_filled`` → 发 ``OrderFilled``
到 ``events.order.<strategy>``（策略 on_order_filled）+ ``events.fills.<instrument>``
（Portfolio 更新持仓）。这样 session 内 Portfolio 与策略持仓视图始终与 DB 真实持仓
一致——拦截后 session 自己收不到 fill，必须回灌，否则策略永远以为空仓。
"""
from __future__ import annotations

import inspect
from typing import Any

from ..execution.exchange import EXECUTION_ENGINE_ENDPOINT
from ..execution.execution_engine import ExecutionEngine
from ..execution.gateway import Gateway
from ..kernel.clock import TestClock
from ..kernel.identifiers import ClientOrderId, InstrumentId, StrategyId, VenueOrderId
from ..kernel.msgbus import MessageBus
from ..model.commands import SubmitOrderCommand
from ..model.data import Bar
from ..model.orders import Order, OrderSide, OrderType
from ..strategy.base import RISK_ENGINE_ENDPOINT, Strategy
from .portfolio import Portfolio
from .position_guard import PositionGuard


class _CaptureGateway(Gateway):
    """收集 ExecutionEngine 转发来的订单，**不撮合**。

    像 ``SimulatedExchange.send_order`` 一样 publish ``internal.venue.accepted``，让
    ExecutionEngine 把 Order 推进到 ACCEPTED（``apply_fill`` 前置状态要求）。
    """

    def __init__(self, msgbus: MessageBus, clock: TestClock) -> None:
        self._msgbus = msgbus
        self._clock = clock
        self._next_id = 1
        self._collected: list[tuple[Order, StrategyId]] = []
        self._unsupported: list[tuple[Order, StrategyId, str]] = []

    def send_order(self, order: Order, strategy_id: StrategyId) -> None:
        # 与 SimulatedExchange.send_order 对等守门：只支持 MARKET / LIMIT。
        # 否则 STOP_MARKET（price=None）会一路走到 OrderExecutor 的 `assert price`
        # 抛 AssertionError，被 runner 最外层 except 吞成 err_streak，连错几次误杀 run。
        # 拒掉走 reject_order 路径 → 记入决策日志，语义清晰。
        if order.type not in (OrderType.MARKET, OrderType.LIMIT):
            reason = f"OrderType {order.type.value} not supported by live runner"
            self._msgbus.publish(
                "internal.venue.rejected",
                {
                    "client_order_id": order.client_order_id,
                    "strategy_id": strategy_id,
                    "reason": reason,
                    "ts": self._clock.now_ns(),
                },
            )
            # 也收集到 _unsupported（issue #43）：否则这类拒单对运维彻底隐形——
            # 策略 on_order_rejected 被通知了，但 live runner 既不记决策行也不写
            # error_log，运维看不到"策略想挂止损单但 live 不支持"。收集后由
            # _process_bar 记一行 rejected 决策（不下单），让用户看见。
            self._unsupported.append((order, strategy_id, reason))
            return
        venue_id = VenueOrderId(f"live-{self._next_id}")
        self._next_id += 1
        self._msgbus.publish(
            "internal.venue.accepted",
            {
                "client_order_id": order.client_order_id,
                "venue_order_id": venue_id,
                "strategy_id": strategy_id,
                "ts": self._clock.now_ns(),
            },
        )
        self._collected.append((order, strategy_id))

    def cancel_order(self, client_order_id: Any) -> None:
        pass

    def take_collected(self) -> list[tuple[Order, StrategyId]]:
        """取走本轮收集到的（可撮合的 MARKET/LIMIT）订单并清空。"""
        out = self._collected[:]
        self._collected.clear()
        return out

    def take_unsupported(self) -> list[tuple[Order, StrategyId, str]]:
        """取走本轮被守门拒掉的不支持单型（含拒因）并清空（issue #43）。"""
        out = self._unsupported[:]
        self._unsupported.clear()
        return out


class LiveEngineSession:
    """单 candidate 单 symbol 的 live 会话：喂 bar → 收集下单意图 → 回灌成交。"""

    def __init__(
        self,
        *,
        strategy_cls: type[Strategy],
        instrument_id: InstrumentId,
        timeframe: str,
        params: dict[str, Any],
        initial_cash: float,
        fee_rate: float,
        protective_stop_loss_pct: float | None = None,
        protective_take_profit_pct: float | None = None,
        protective_trailing_stop_pct: float | None = None,
    ) -> None:
        self.clock = TestClock(0)
        self.msgbus = MessageBus()
        self._gateway = _CaptureGateway(self.msgbus, self.clock)
        # ExecutionEngine 注册 EXECUTION_ENGINE_ENDPOINT + 订阅 internal.venue.*
        self.execution_engine = ExecutionEngine(self.msgbus, self._gateway)
        self.portfolio = Portfolio(self.msgbus, initial_cash=initial_cash, fee_rate=fee_rate)
        # 风控旁路：策略 submit_order → RISK_ENGINE_ENDPOINT → 直接转 ExecutionEngine。
        # 真风控在外部 route_through_plan_exec 用 DB-backed RiskGuard 做。
        self.msgbus.register_endpoint(RISK_ENGINE_ENDPOINT, self._forward_to_execution)

        self.instrument_id = instrument_id
        self.timeframe = timeframe
        self._initial_cash = initial_cash
        self._strategy = self._build_strategy(strategy_cls, params, initial_cash)
        self._strategy.on_start()  # 策略在此订阅 bars（subscribe_bars）

        # ADR-0052：框架级持仓保护止损（与回测引擎共用同一组件，行为一致）。三阈值全
        # None → None（退化为无 guard）。出场单直发 ExecutionEngine → 被 _CaptureGateway
        # 收走，与策略单一起由 runner 走护栏 plan/exec 路由（runner 对保护性 tag 跳过开仓闸）。
        self._guard = PositionGuard.from_thresholds(
            self.msgbus,
            self.clock,
            self.portfolio,
            stop_loss_pct=protective_stop_loss_pct,
            take_profit_pct=protective_take_profit_pct,
            trailing_stop_pct=protective_trailing_stop_pct,
        )
        if self._guard is not None:
            self._guard.bind_strategy(self._strategy.strategy_id)

    # ─── 内部组装 ───

    def _forward_to_execution(self, cmd: SubmitOrderCommand) -> None:
        self.msgbus.send(EXECUTION_ENGINE_ENDPOINT, cmd)

    def _build_strategy(
        self,
        strategy_cls: type[Strategy],
        params: dict[str, Any],
        initial_cash: float,
    ) -> Strategy:
        """复刻 runner.run_engine_in_subprocess 的 kwargs 注入。"""
        kwargs: dict[str, Any] = dict(params)
        try:
            sig = inspect.signature(strategy_cls.__init__)
            if "initial_cash" in sig.parameters and "initial_cash" not in kwargs:
                kwargs["initial_cash"] = initial_cash
            if "position_pct" in sig.parameters and "position_pct" not in kwargs:
                kwargs["position_pct"] = 1.0
        except (TypeError, ValueError):
            pass
        return strategy_cls(  # type: ignore[call-arg]
            name=f"{strategy_cls.__name__}-{self.instrument_id.symbol}-live",
            clock=self.clock,
            msgbus=self.msgbus,
            instrument_id=self.instrument_id,
            timeframe=self.timeframe,
            **kwargs,
        )

    # ─── 驱动 ───

    def feed_bar(self, bar: Bar) -> list[tuple[Order, StrategyId]]:
        """喂一根 bar：推进时间 + update_mark + 触发 on_bar；返回本根 bar 策略提交的订单。"""
        if bar.ts_event > self.clock.now_ns():
            self.clock.set_time(bar.ts_event)
        self.portfolio.update_mark(bar.instrument_id, bar.close)
        # ADR-0052：框架级持仓保护止损在 mark 更新后判定（与回测引擎同点）；保护性出场单
        # 被 _CaptureGateway 收走，与策略单一起在下方 take_collected 返回给 runner 路由。
        if self._guard is not None:
            self._guard.evaluate(bar)
        topic = (
            f"data.bars.{bar.instrument_id.venue}."
            f"{bar.instrument_id.symbol}.{bar.timeframe}"
        )
        self.msgbus.publish(topic, bar)
        return self._gateway.take_collected()

    def take_unsupported_orders(self) -> list[tuple[Order, StrategyId, str]]:
        """取走本根 bar 被守门拒掉的不支持单型（含拒因），供 runner 记决策行（issue #43）。

        必须每根 bar 在 ``feed_bar`` 后排空（即便丢弃），否则会跨 bar 泄漏到下一根。
        """
        return self._gateway.take_unsupported()

    def confirm_fill(
        self,
        *,
        order: Order,
        strategy_id: StrategyId,
        fill_qty: float,
        fill_price: float,
        ts_event: int,
    ) -> None:
        """外部 plan/exec 成交后回灌 → ExecutionEngine 发 OrderFilled → 策略 + Portfolio 更新。"""
        self.msgbus.publish(
            "internal.venue.filled",
            {
                "client_order_id": order.client_order_id,
                "strategy_id": strategy_id,
                "instrument_id": order.instrument_id,
                "side": order.side,
                "fill_qty": fill_qty,
                "fill_price": fill_price,
                "trade_id": f"live-{order.client_order_id}",
                "ts": ts_event,
            },
        )

    def reject_order(
        self,
        *,
        order: Order,
        strategy_id: StrategyId,
        reason: str,
        ts_event: int,
    ) -> None:
        """外部拒单（风控拒 / 限价未触发）→ 回灌 rejected，清理 ExecutionEngine 内存状态。"""
        self.msgbus.publish(
            "internal.venue.rejected",
            {
                "client_order_id": order.client_order_id,
                "strategy_id": strategy_id,
                "reason": reason,
                "ts": ts_event,
            },
        )

    def restore_position(
        self,
        *,
        quantity_signed: float,
        avg_price: float,
        ts_event: int,
    ) -> None:
        """resume 时把 DB 当前持仓灌回 session（issue #37.2 / #46）。

        合成一笔成交穿过完整 EE 周期（submit → accept → fill），让 **Portfolio** 与
        **策略持仓视图**都更新到目标持仓：``confirm_fill`` 触发 ``OrderFilled`` →
        ``events.fills.<instrument>`` → Portfolio 更新 + 发 ``events.position.<strategy>``
        → 策略 ``on_position_opened``（内置 sma_cross 等惯用此 hook 跟踪 long/flat）。
        ``avg_price`` 灌成持仓 avg，未实现盈亏从 0 起，首根真 bar 的 mark 再更新。

        局限：策略若用非标准内部 flag（不订阅 position/fill 事件）跟踪持仓，无法还原其
        私有状态——但 Inalpha 契约 / 模板引导走 position/fill hook，主流策略可正确续跑。
        ``quantity_signed == 0`` 时 no-op。
        """
        if quantity_signed == 0:
            return
        side = OrderSide.BUY if quantity_signed > 0 else OrderSide.SELL
        qty = abs(quantity_signed)
        order = Order(
            client_order_id=ClientOrderId(f"restore-{self.instrument_id.symbol}-{ts_event}"),
            instrument_id=self.instrument_id,
            side=side,
            type=OrderType.MARKET,
            quantity=qty,
        )
        sid = self._strategy.strategy_id
        if ts_event > self.clock.now_ns():
            self.clock.set_time(ts_event)
        # 直发 EXECUTION_ENGINE_ENDPOINT（绕过风控，这是重建非新意图）：EE._submit 把单
        # 落入 _orders + 经 CaptureGateway publish accepted。是 _handle_filled 的前置态。
        self.msgbus.send(
            EXECUTION_ENGINE_ENDPOINT,
            SubmitOrderCommand(order=order, strategy_id=sid, ts_init=ts_event),
        )
        # 排空 gateway 收集到的这笔重建单，绝不让它被 runner 当新意图路由去下单
        self._gateway.take_collected()
        self._gateway.take_unsupported()
        # 确认成交 at avg_price → OrderFilled → Portfolio + 策略持仓视图同步更新
        self.confirm_fill(
            order=order, strategy_id=sid,
            fill_qty=qty, fill_price=avg_price, ts_event=ts_event,
        )
        self.portfolio.update_mark(self.instrument_id, avg_price)

    def cumulative_pnl(self) -> float:
        """会话累计盈亏（mark-to-market 总权益 − 初始现金）。"""
        return self.portfolio.equity() - self._initial_cash
