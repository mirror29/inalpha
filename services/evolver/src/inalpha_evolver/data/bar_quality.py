"""Frozen bar close, calendar-grid, and content-hash validation."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from inalpha_paper.bar_conversion import bar_from_dict
from inalpha_paper.kernel.identifiers import InstrumentId
from inalpha_paper.market_cutoff import expected_bar_timestamps, expected_latest_bar_open
from inalpha_paper.market_evaluation import MarketEvaluationContext
from inalpha_paper.model.data import Bar
from inalpha_shared.errors import ValidationError

from .bar_hash import bars_content_hash
from .bar_validation import validate_identity, validate_values


def prepare_frozen_bars(
    raw_bars: list[dict[str, Any]],
    *,
    instrument_id: InstrumentId,
    context: MarketEvaluationContext,
    as_of: datetime,
) -> tuple[tuple[Bar, ...], str, float, datetime]:
    """Remove forming bars and validate a complete connector calendar grid."""
    now = _utc(as_of)
    cutoff = expected_latest_bar_open(context, now)
    bars: list[Bar] = []
    previous_ts = -1
    for raw in raw_bars:
        validate_identity(raw, instrument_id, context.data_timeframe)
        bar = bar_from_dict(raw, instrument_id, context.canonical_timeframe)
        if _bar_datetime(bar) > cutoff:
            continue
        if bar.ts_event <= previous_ts:
            raise ValidationError(
                "bars must be strictly increasing without duplicates",
                code="EVOLUTION_DATA_ORDER_INVALID",
            )
        validate_values(bar)
        bars.append(bar)
        previous_ts = bar.ts_event
    if len(bars) < 2:
        raise ValidationError(
            f"evolution needs at least 2 closed bars, got {len(bars)}",
            code="EVOLUTION_NO_CLOSED_BARS",
        )
    latest = _bar_datetime(bars[-1])
    lag = max(0.0, (cutoff - latest).total_seconds())
    if lag > 0:
        raise ValidationError(
            f"latest closed bar is {lag:.0f}s behind expected cutoff",
            code="EVOLUTION_DATA_FRESHNESS_FAILED",
            details={
                "latest_bar_ts": latest.isoformat(),
                "cutoff_bar_ts": cutoff.isoformat(),
                "lag_seconds": lag,
            },
        )
    _validate_grid(bars, context, now)
    return tuple(bars), bars_content_hash(bars, instrument_id, context), lag, cutoff


def _validate_grid(
    bars: list[Bar],
    context: MarketEvaluationContext,
    as_of: datetime,
) -> None:
    expected = expected_bar_timestamps(context, _bar_datetime(bars[0]), as_of)
    actual = tuple(_bar_datetime(bar) for bar in bars)
    expected_set, actual_set = set(expected), set(actual)
    missing = tuple(value for value in expected if value not in actual_set)
    unexpected = tuple(value for value in actual if value not in expected_set)
    if missing or unexpected:
        raise ValidationError(
            "bars do not match the connector calendar grid",
            code="EVOLUTION_DATA_GAP_INVALID",
            details={
                "missing": [value.isoformat() for value in missing[:20]],
                "unexpected": [value.isoformat() for value in unexpected[:20]],
            },
        )


def _bar_datetime(bar: Bar) -> datetime:
    return datetime.fromtimestamp(bar.bar_open_at / 1_000_000_000, tz=UTC)


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
