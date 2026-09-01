"""Deterministic HypothesisSpec to sandboxed Strategy source compiler."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from .models import HypothesisSpec


@dataclass(frozen=True, slots=True)
class CompiledHypothesis:
    """Auditable compiler output and its stable identity."""

    spec: HypothesisSpec
    source_code: str
    source_hash: str
    compiler_version: str


def expand_implementations(spec: HypothesisSpec) -> list[HypothesisSpec]:
    """Expand one strong-event hypothesis into direct/confirmed/hybrid ablation arms."""
    direct_allowed = set(spec.event_types) <= {"listing", "delisting", "exploit", "chain_halt"}
    modes = (
        ["direct", "confirmed", "hybrid"]
        if direct_allowed
        else [
            "confirmed",
            "hybrid",
            "confirmed",
        ]
    )
    profiles: list[HypothesisSpec] = []
    for index, mode in enumerate(modes):
        update: dict[str, object] = {"trigger_mode": mode}
        if not direct_allowed and index == 2:
            update["confirmation"] = spec.confirmation.model_copy(
                update={
                    "min_price_change_pct": spec.confirmation.min_price_change_pct * 1.5,
                    "min_volume_ratio": spec.confirmation.min_volume_ratio * 1.25,
                }
            )
        profiles.append(spec.model_copy(deep=True, update=update))
    return profiles


def compile_hypothesis(spec: HypothesisSpec) -> CompiledHypothesis:
    """Compile a validated DSL object; no LLM output is interpolated as executable text."""
    class_suffix = hashlib.sha256(
        spec.model_dump_json(exclude={"hypothesis_id"}).encode("utf-8")
    ).hexdigest()[:12]
    class_name = f"EventHypothesis_{class_suffix}"
    event_types = repr(tuple(spec.event_types))
    assets = repr(tuple(spec.assets))
    direction = repr(spec.direction)
    trigger_mode = repr(spec.trigger_mode)
    source = f"""class {class_name}(Strategy):
    def __init__(self, name, clock, msgbus, instrument_id, timeframe="1h", initial_cash=10000.0, position_pct={spec.risk.position_pct!r}):
        super().__init__(name, clock, msgbus)
        self._instrument_id = instrument_id
        self._timeframe = timeframe
        self._initial_cash = float(initial_cash)
        self._position_pct = min(float(position_pct), {spec.risk.position_pct!r})
        self._asset = str(instrument_id.symbol).split("/")[0].upper()
        self._event_types = {event_types}
        self._assets = {assets}
        self._direction = {direction}
        self._trigger_mode = {trigger_mode}
        self._closes = deque(maxlen={spec.confirmation.lookback_bars})
        self._volumes = deque(maxlen={spec.confirmation.lookback_bars})
        self._last_bar = None
        self._pending_event = None
        self._pending_age = 0
        self._holding_age = 0
        self._position_qty = 0.0
        self._entry_price = 0.0
        self._initial_sent = False

    def on_start(self):
        self.subscribe_bars(self._instrument_id, self._timeframe)

    def on_market_event(self, event):
        if event.event_type not in self._event_types:
            return
        if self._assets and self._asset not in self._assets:
            return
        if event.assets and self._asset not in event.assets:
            return
        if event.severity < {spec.risk.min_severity!r} or event.confidence < {spec.risk.min_confidence!r}:
            return
        self._pending_event = event
        self._pending_age = 0
        self._initial_sent = False
        if self._trigger_mode == "direct" and self._last_bar is not None:
            self._enter(self._last_bar, 1.0)
            self._pending_event = None
        elif self._trigger_mode == "hybrid" and self._last_bar is not None:
            self._enter(self._last_bar, {spec.risk.hybrid_initial_fraction!r})
            self._initial_sent = True

    def on_bar(self, bar):
        if bar.instrument_id != self._instrument_id or bar.timeframe != self._timeframe:
            return
        previous_close = self._closes[-1] if self._closes else None
        average_volume = sum(self._volumes) / len(self._volumes) if self._volumes else None
        self._closes.append(bar.close)
        self._volumes.append(bar.volume)
        self._last_bar = bar
        if self._position_qty != 0.0:
            self._holding_age += 1
            adverse = ((bar.close / self._entry_price) - 1.0) * 100.0
            if self._position_qty < 0:
                adverse = -adverse
            if adverse <= -{spec.invalidation.max_adverse_pct!r} or self._holding_age >= {spec.invalidation.holding_bars}:
                self._exit()
        if self._pending_event is None:
            return
        self._pending_age += 1
        if self._pending_age > {spec.invalidation.ttl_bars}:
            self._pending_event = None
            return
        if self._trigger_mode == "direct":
            if self._position_qty == 0.0:
                self._enter(bar, 1.0)
            self._pending_event = None
            return
        if previous_close is None or average_volume is None or previous_close <= 0 or average_volume <= 0:
            return
        change = ((bar.close / previous_close) - 1.0) * 100.0
        if self._direction == "short":
            change = -change
        confirmed = change >= {spec.confirmation.min_price_change_pct!r} and bar.volume / average_volume >= {spec.confirmation.min_volume_ratio!r}
        if not confirmed:
            return
        fraction = 1.0 - {spec.risk.hybrid_initial_fraction!r} if self._trigger_mode == "hybrid" and self._initial_sent else 1.0
        self._enter(bar, fraction)
        self._pending_event = None

    def on_position_opened(self, event):
        self._position_qty = float(event.quantity)
        self._entry_price = float(event.avg_open_price)
        self._holding_age = 0

    def on_position_changed(self, event):
        self._position_qty = float(event.quantity)
        self._entry_price = float(event.avg_open_price)

    def on_position_closed(self, event):
        self._position_qty = 0.0
        self._entry_price = 0.0
        self._holding_age = 0

    def _enter(self, bar, fraction):
        if bar.close <= 0 or fraction <= 0:
            return
        quantity = self._initial_cash * self._position_pct * fraction / bar.close / 1.10
        side = OrderSide.BUY if self._direction == "long" else OrderSide.SELL
        self.submit_order(Order(client_order_id=ClientOrderId("event-entry-" + uuid4().hex[:12]), instrument_id=self._instrument_id, side=side, type=OrderType.MARKET, quantity=quantity))

    def _exit(self):
        if self._position_qty == 0.0:
            return
        side = OrderSide.SELL if self._position_qty > 0 else OrderSide.BUY
        self.submit_order(Order(client_order_id=ClientOrderId("event-exit-" + uuid4().hex[:12]), instrument_id=self._instrument_id, side=side, type=OrderType.MARKET, quantity=abs(self._position_qty)))
"""
    source_hash = hashlib.sha256(source.encode("utf-8")).hexdigest()
    return CompiledHypothesis(
        spec=spec,
        source_code=source,
        source_hash=source_hash,
        compiler_version=spec.compiler_version,
    )


def canonical_spec_hash(spec: HypothesisSpec) -> str:
    """Return a stable genotype hash excluding its storage identity."""
    payload = spec.model_dump(mode="json", exclude={"hypothesis_id"})
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


__all__ = [
    "CompiledHypothesis",
    "canonical_spec_hash",
    "compile_hypothesis",
    "expand_implementations",
]
