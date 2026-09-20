"""data-service bar 响应到 paper 内核模型的转换。"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from .kernel.clock import datetime_to_ns
from .kernel.identifiers import InstrumentId
from .market_evaluation import canonical_timeframe, fixed_timeframe_seconds
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
    else:
        timestamp = timestamp.astimezone(UTC)
    ts_open_ns = datetime_to_ns(timestamp)
    timeframe_seconds = fixed_timeframe_seconds(timeframe)
    if timeframe_seconds is None and canonical_timeframe(timeframe) == "1M":
        next_year = timestamp.year + (1 if timestamp.month == 12 else 0)
        next_month = 1 if timestamp.month == 12 else timestamp.month + 1
        ts_known_ns = datetime_to_ns(datetime(next_year, next_month, 1, tzinfo=UTC))
    elif timeframe_seconds is not None:
        ts_known_ns = ts_open_ns + timeframe_seconds * 1_000_000_000
    else:  # pragma: no cover - guarded by ``fixed_timeframe_seconds`` validation
        raise ValueError(f"timeframe {timeframe!r} has no bar-known offset")
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
