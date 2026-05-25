"""SignalReplayStrategy —— 重放预生成的 ``signals`` 序列。

**给 E1 evolution loop 用**：LLM 在沙盒里 ``generate_signals(bars) -> list[signal]``
跑出整段 signals，然后用本策略喂给 ``BacktestEngine``，**复用整套撮合 + portfolio + metrics**。

这样 LLM 不需要在沙盒里维护策略状态（订阅 bars / kernel clock / msgbus），
只需要写**纯函数**。Inalpha runtime 把它 adapt 成 event-driven Strategy。

签名：

    SignalReplayStrategy(
        name, clock, msgbus, instrument_id,
        timeframe="1h",
        signals=[{"ts": 1700000000000, "side": "BUY", "qty": 0.01}, ...],
    )

- ``signals[].ts`` 单位 **毫秒**（strategy_v1 contract 定义）
- ``Bar.ts_event`` 单位 **纳秒**（Inalpha kernel 约定），内部转 ms 后比较
- 触发规则：``signal.ts <= bar.ts_event_ms`` 时发对应 market order
  → 同 bar 内多个 signal 也会全部发出，按顺序

**何时不用**：
- 想让策略实时观察 bar 决策 → 写继承 Strategy 的真策略（sma_cross 范式）
- signals 数量超大（>10k）→ 走 batch 注入接口（D-10+ 考虑）
"""
from __future__ import annotations

from collections import deque
from typing import Any
from uuid import uuid4

from ..kernel.clock import Clock
from ..kernel.identifiers import ClientOrderId, InstrumentId
from ..kernel.msgbus import MessageBus
from ..model.data import Bar
from ..model.orders import Order, OrderSide, OrderType
from ..strategy.base import Strategy

# Bar.ts_event 是 ns；signal.ts 是 ms（strategy_v1 contract）
_NS_PER_MS = 1_000_000


def _parse_side(raw: Any) -> OrderSide:
    """大小写不敏感地把字符串翻成 OrderSide。"""
    if isinstance(raw, OrderSide):
        return raw
    s = str(raw).upper()
    if s == "BUY":
        return OrderSide.BUY
    if s == "SELL":
        return OrderSide.SELL
    raise ValueError(f"signal.side 必须是 'BUY' 或 'SELL'，得到 {raw!r}")


class SignalReplayStrategy(Strategy):
    """重放预生成的 ``signals`` 序列到 ``BacktestEngine``。

    Args:
        name: 策略实例名（runner 一般填 ``"signal_replay-BTC/USDT"``）
        clock / msgbus: 内核注入
        instrument_id: 目标标的（必须跟 signals 的标的对得上）
        timeframe: 订阅哪个 timeframe 的 bar；默认 ``1h``
        signals: list[dict]，每个 dict 至少含 ``ts (ms, int)`` / ``side (BUY|SELL)`` /
            ``qty (float > 0)``。FastAPI 经 ``params: dict[str, Any]`` 透传过来时
            是普通 list[dict]；本类构造时校验 + 排序 + 转 deque。

    Raises:
        ValueError: signal 字段缺失 / 类型错 / qty 非正。
    """

    def __init__(
        self,
        name: str,
        clock: Clock,
        msgbus: MessageBus,
        instrument_id: InstrumentId,
        timeframe: str = "1h",
        signals: list[dict[str, Any]] | None = None,
    ) -> None:
        super().__init__(name, clock, msgbus)
        self._instrument_id = instrument_id
        self._timeframe = timeframe

        raw_signals = signals or []
        normalized: list[tuple[int, OrderSide, float]] = []
        for i, sig in enumerate(raw_signals):
            if not isinstance(sig, dict):
                raise ValueError(f"signals[{i}] 必须是 dict，得到 {type(sig).__name__}")
            try:
                ts_ms = int(sig["ts"])
                side = _parse_side(sig["side"])
                qty = float(sig["qty"])
            except KeyError as e:
                raise ValueError(f"signals[{i}] 缺字段 {e}") from None
            if qty <= 0:
                raise ValueError(f"signals[{i}].qty 必须 > 0，得到 {qty}")
            normalized.append((ts_ms, side, qty))

        # 按 ts 升序消费 —— deque popleft O(1)
        normalized.sort(key=lambda x: x[0])
        self._pending: deque[tuple[int, OrderSide, float]] = deque(normalized)

        # 暴露统计（测试 / 监控用）
        self.replayed_count: int = 0
        self.initial_signal_count: int = len(normalized)

    def on_start(self) -> None:
        self.subscribe_bars(self._instrument_id, self._timeframe)

    def on_bar(self, bar: Bar) -> None:
        if bar.instrument_id != self._instrument_id or bar.timeframe != self._timeframe:
            return

        bar_ts_ms = bar.ts_event // _NS_PER_MS
        # 把所有 ts <= 当前 bar ts 的 signal 全部发出（按时间顺序）
        while self._pending and self._pending[0][0] <= bar_ts_ms:
            _ts, side, qty = self._pending.popleft()
            self._submit_market(side, qty)
            self.replayed_count += 1

    # ─── 内部 ───

    def _submit_market(self, side: OrderSide, qty: float) -> None:
        order = Order(
            client_order_id=ClientOrderId(f"replay-{self.name}-{uuid4().hex[:8]}"),
            instrument_id=self._instrument_id,
            side=side,
            type=OrderType.MARKET,
            quantity=qty,
        )
        self.submit_order(order)
