"""``RiskEngine`` —— 执行层风控编排器。

D-5 起为 pass-through 占位；[ADR-0006](../../../../docs/miro/decisions/0006-risk-rules.md)
立项后加入 `RiskRule` 列表 + `LockStore` 持久化，对 `SubmitOrderCommand` 做前置闸门。

行为：

- `rules=None` 或空列表 → 退化为 pass-through（向后兼容 D-5 ~ D-8 老调用方）
- 提供 `rules` + `clock` → 每条 `SubmitOrderCommand` 走 2 段：
  1. **复用已有锁**：先查 LockStore 看 global / market / symbol 是否已有 active lock
  2. **跑 rules**：3 层（global / market / symbol）规则；任意命中即写入 LockStore
- 命中即 publish `OrderRejected` 到 `events.order.<strategy_id>` topic，**不转发** 到 ExecutionEngine

[refs/freqtrade.md §6.2](../../../../docs/miro/refs/freqtrade.md) 是设计灵感源（GPL-3.0，
本文件只借鉴设计，不复制源码）。
"""
from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from ..kernel.identifiers import InstrumentId
from ..kernel.msgbus import MessageBus
from ..model.commands import CancelOrderCommand, ModifyOrderCommand, SubmitOrderCommand
from ..model.events import OrderRejected
from ..model.orders import OrderSide
from ..strategy.base import RISK_ENGINE_ENDPOINT
from .exchange import EXECUTION_ENGINE_ENDPOINT
from .risk_rules import InMemoryLockStore, LockStore, RiskRule, RiskVerdict
from .risk_rules.base import Side
from .risk_rules.exchange_resolver import resolve_calendar_code
from .risk_rules.lock_store import ActiveLock

if TYPE_CHECKING:
    from ..kernel.clock import Clock


class RiskEngine:
    """执行层风控编排器。"""

    def __init__(
        self,
        msgbus: MessageBus,
        *,
        rules: list[RiskRule] | None = None,
        clock: Clock | None = None,
        starting_balance: float = 10_000.0,
        lock_store: LockStore | None = None,
    ) -> None:
        self._msgbus = msgbus
        self._rules: list[RiskRule] = list(rules) if rules else []
        self._clock = clock
        self._starting_balance = starting_balance
        self._lock_store: LockStore = lock_store or InMemoryLockStore()

        if self._rules and self._clock is None:
            raise ValueError(
                "RiskEngine: 提供 rules 时必须同时提供 clock（rule 的 check_* 需要 now）"
            )

        msgbus.register_endpoint(RISK_ENGINE_ENDPOINT, self._handle)

    # ─── 命令处理 ───

    def _handle(self, msg: object) -> None:
        if isinstance(msg, SubmitOrderCommand) and self._rules:
            if self._check_and_maybe_reject(msg):
                return
        if isinstance(msg, SubmitOrderCommand | CancelOrderCommand | ModifyOrderCommand):
            self._msgbus.send(EXECUTION_ENGINE_ENDPOINT, msg)

    # ─── 规则编排 ───

    def _check_and_maybe_reject(self, cmd: SubmitOrderCommand) -> bool:
        """先看已有锁；没锁则跑 rules。命中即 publish OrderRejected 并返回 True。"""
        assert self._clock is not None  # __init__ 校验
        now = self._clock.now()
        order = cmd.order
        side: Side = "long" if order.side == OrderSide.BUY else "short"

        # 1. 先看 LockStore 现有锁（顺序：global > market > symbol）
        for scope in ("global", "market", "symbol"):
            kwargs: dict[str, object] = {"side": side}
            if scope == "market":
                # market 锁键用交易所日历 code，无法解析时 fallback venue
                kwargs["market"] = (
                    resolve_calendar_code(
                        order.instrument_id.venue, order.instrument_id.symbol
                    )
                    or order.instrument_id.venue
                )
            elif scope == "symbol":
                kwargs["symbol"] = str(order.instrument_id)
            existing = self._lock_store.is_locked(now, scope=scope, **kwargs)  # type: ignore[arg-type]
            if existing is not None:
                self._publish_rejection_from_lock(cmd, existing)
                return True

        # 2. 没现有锁 → 跑 rules
        balance = self._starting_balance

        for rule in self._rules:
            if rule.has_global_check:
                verdict = rule.check_global(now, side, balance)
                if verdict is not None:
                    self._record_and_reject(cmd, verdict, instrument_id=None, now=now)
                    return True

        for rule in self._rules:
            if rule.has_market_check:
                verdict = rule.check_market(order.instrument_id, now, side, balance)
                if verdict is not None:
                    self._record_and_reject(cmd, verdict, instrument_id=None, now=now)
                    return True

        for rule in self._rules:
            if rule.has_symbol_check:
                verdict = rule.check_symbol(order.instrument_id, now, side, balance)
                if verdict is not None:
                    self._record_and_reject(
                        cmd, verdict, instrument_id=order.instrument_id, now=now
                    )
                    return True

        return False

    def _record_and_reject(
        self,
        cmd: SubmitOrderCommand,
        verdict: RiskVerdict,
        *,
        instrument_id: InstrumentId | None,
        now: datetime,
    ) -> None:
        """rule 命中：写入 LockStore + publish OrderRejected。"""
        self._lock_store.add(verdict, instrument_id=instrument_id, now=now)
        reason = f"[{verdict.rule_name}] {verdict.reason}"
        self._publish_order_rejected(cmd, reason)

    def _publish_rejection_from_lock(
        self, cmd: SubmitOrderCommand, lock: ActiveLock
    ) -> None:
        """existing lock 命中：直接 publish，**不**重新写 store。"""
        reason = f"[{lock.rule_name}] {lock.reason}（已锁，至 {lock.locked_until.isoformat()}）"
        self._publish_order_rejected(cmd, reason)

    def _publish_order_rejected(self, cmd: SubmitOrderCommand, reason: str) -> None:
        self._msgbus.publish(
            f"events.order.{cmd.strategy_id}",
            OrderRejected(
                client_order_id=cmd.order.client_order_id,
                strategy_id=cmd.strategy_id,
                ts_event=cmd.ts_init,
                ts_init=cmd.ts_init,
                reason=reason,
            ),
        )

    # ─── inspection（测试用） ───

    @property
    def rule_names(self) -> list[str]:
        return [r.name for r in self._rules]

    @property
    def lock_store(self) -> LockStore:
        return self._lock_store
