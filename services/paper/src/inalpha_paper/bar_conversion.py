"""data-service bar 响应到 paper 内核模型的转换。"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from .kernel.clock import datetime_to_ns
from .kernel.identifiers import InstrumentId
from .market_evaluation import fixed_timeframe_seconds
from .model.data import Bar


def bar_from_dict(
    data: dict[str, Any],
    instrument_id: InstrumentId,
    timeframe: str,
) -> Bar:
    """把 data-service ``BarResponse`` 转成内核 ``Bar``。"""
    raw_ts = data["ts"]
    timestamp = (
        datetime.fromisoformat(raw_ts.replace("Z", "+00:00")) if isinstance(raw_ts, str) else raw_ts
    )
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=UTC)
    ts_open_ns = datetime_to_ns(timestamp)
    timeframe_seconds = fixed_timeframe_seconds(timeframe)
    if timeframe_seconds is None:
        raise ValueError(f"timeframe {timeframe!r} has no fixed bar-known offset")
    ts_known_ns = ts_open_ns + timeframe_seconds * 1_000_000_000
    return Bar(
        instrument_id=instrument_id,
        timeframe=timeframe,
        open=float(data["open"]),
        high=float(data["high"]),
        low=float(data["low"]),
        close=float(data["close"]),
        volume=float(data["volume"]),
        ts_event=ts_known_ns,
        ts_init=ts_known_ns,
        ts_open=ts_open_ns,
    )


__all__ = ["bar_from_dict"]
