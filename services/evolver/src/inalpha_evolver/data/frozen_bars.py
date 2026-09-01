from __future__ import annotations

from datetime import UTC, datetime

from inalpha_paper.data_client import DataClient
from inalpha_paper.kernel.identifiers import InstrumentId
from inalpha_paper.market_evaluation import build_market_evaluation_context
from inalpha_shared.errors import ValidationError

from .backfill_audit import backfill_snapshot
from .bar_quality import prepare_frozen_bars
from .datetime_policy import normalize_datetime, reject_future_as_of
from .frozen_io import backfill, read_bars
from .manifest import DatasetManifest, FrozenDataset


class FrozenBarsLoader:
    def __init__(self, data_client: DataClient) -> None:
        self._data_client = data_client

    async def load(
        self,
        *,
        venue: str,
        symbol: str,
        timeframe: str,
        from_ts: datetime,
        as_of: datetime,
    ) -> FrozenDataset:
        start = normalize_datetime(from_ts, field="from_ts", require_aware=False)
        cutoff = normalize_datetime(as_of, field="as_of", require_aware=True)
        reject_future_as_of(cutoff)
        if start >= cutoff:
            raise ValidationError(
                "evolution from_ts must be earlier than as_of",
                code="EVOLUTION_DATA_RANGE_INVALID",
            )
        context = build_market_evaluation_context(
            venue=venue, symbol=symbol, timeframe=timeframe, as_of=cutoff
        )
        backfill_result = await backfill(
            self._data_client, venue, symbol, context.data_timeframe, start, cutoff
        )
        raw_bars = await read_bars(
            self._data_client, venue, symbol, context.data_timeframe, start, cutoff
        )
        if len(raw_bars) > 10_000:
            raise ValidationError(
                "evolution dataset exceeds 10000 bars",
                code="EVOLUTION_DATA_LIMIT_EXCEEDED",
            )
        bars, content_hash, lag, bar_cutoff = prepare_frozen_bars(
            raw_bars,
            instrument_id=InstrumentId(symbol=symbol, venue=venue),
            context=context,
            as_of=cutoff,
        )
        first, latest = _bar_time(bars[0].bar_open_at), _bar_time(bars[-1].bar_open_at)
        manifest = DatasetManifest(
            venue=venue,
            symbol=symbol,
            requested_timeframe=timeframe,
            data_timeframe=context.data_timeframe,
            canonical_timeframe=context.canonical_timeframe,
            requested_from=start,
            requested_as_of=cutoff,
            effective_from=first,
            effective_to=latest,
            latest_bar_ts=latest,
            cutoff_bar_ts=bar_cutoff,
            freshness_lag_seconds=lag,
            data_epoch=int(latest.timestamp() * 1000),
            bar_count=len(bars),
            annualization_periods=context.annualization_periods,
            calendar_code=context.calendar_code,
            content_sha256=content_hash,
            backfill=backfill_snapshot(backfill_result),
        )
        return FrozenDataset(bars=bars, manifest=manifest)


def _bar_time(value: int) -> datetime:
    return datetime.fromtimestamp(value / 1_000_000_000, tz=UTC)


__all__ = ["FrozenBarsLoader"]
