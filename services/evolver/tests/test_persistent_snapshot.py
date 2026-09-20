"""Immutable E2 dataset snapshot tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from inalpha_paper.kernel.identifiers import InstrumentId
from inalpha_paper.model.data import Bar

from inalpha_evolver.data.manifest import BackfillSnapshot, DatasetManifest, FrozenDataset
from inalpha_evolver.data.persistent_snapshot import (
    CANONICAL_VERSION,
    decode_frozen_dataset,
    discovery_dataset,
    encode_frozen_dataset,
)


def _dataset() -> FrozenDataset:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    instrument = InstrumentId(symbol="BTC/USDT", venue="binance")
    bars = tuple(
        Bar(
            instrument_id=instrument,
            timeframe="1h",
            open=100 + index,
            high=101 + index,
            low=99 + index,
            close=100.5 + index,
            volume=1_000 + index,
            ts_open=int((start + timedelta(hours=index)).timestamp() * 1e9),
            ts_event=int((start + timedelta(hours=index + 1)).timestamp() * 1e9),
            ts_init=int((start + timedelta(hours=index + 1)).timestamp() * 1e9),
        )
        for index in range(10)
    )
    manifest = DatasetManifest(
        venue="binance",
        symbol="BTC/USDT",
        requested_timeframe="1h",
        data_timeframe="1h",
        canonical_timeframe="1h",
        requested_from=start,
        requested_as_of=start + timedelta(hours=11),
        effective_from=start,
        effective_to=start + timedelta(hours=9),
        latest_bar_ts=start + timedelta(hours=9),
        cutoff_bar_ts=start + timedelta(hours=9),
        freshness_lag_seconds=0,
        data_epoch=1,
        bar_count=10,
        annualization_periods=8_760,
        calendar_code=None,
        content_sha256="a" * 64,
        backfill=BackfillSnapshot(
            venue="binance",
            symbol="BTC/USDT",
            timeframe="1h",
            bars_fetched=10,
            bars_inserted=10,
            from_ts=start,
            to_ts=start + timedelta(hours=11),
        ),
    )
    return FrozenDataset(bars=bars, manifest=manifest)


def test_dataset_snapshot_encoding_is_deterministic_and_round_trips() -> None:
    dataset = _dataset()
    first_payload, first_hash = encode_frozen_dataset(dataset)
    second_payload, second_hash = encode_frozen_dataset(dataset)
    assert first_payload == second_payload
    assert first_hash == second_hash

    restored = decode_frozen_dataset({
        "compressed_payload": first_payload,
        "content_sha256": first_hash,
        "canonical_version": CANONICAL_VERSION,
        "bar_count": 10,
        "discovery_end": 6,
        "validation_end": 8,
        "venue": "binance",
        "symbol": "BTC/USDT",
        "timeframe": "1h",
    })
    assert restored == dataset


def test_dataset_snapshot_fails_closed_on_payload_or_split_drift() -> None:
    payload, digest = encode_frozen_dataset(_dataset())
    base = {
        "compressed_payload": payload,
        "content_sha256": digest,
        "bar_count": 10,
        "discovery_end": 6,
        "validation_end": 8,
        "venue": "binance",
        "symbol": "BTC/USDT",
        "timeframe": "1h",
    }
    with pytest.raises(RuntimeError, match="hash mismatch"):
        decode_frozen_dataset(base | {"content_sha256": "0" * 64})
    with pytest.raises(RuntimeError, match="split boundary"):
        decode_frozen_dataset(base | {"validation_end": 9})


def test_discovery_view_excludes_later_bars_and_future_manifest_metadata() -> None:
    payload, digest = encode_frozen_dataset(_dataset())
    row = {
        "compressed_payload": payload, "content_sha256": digest, "bar_count": 10,
        "discovery_end": 6, "validation_end": 8, "venue": "binance",
        "symbol": "BTC/USDT", "timeframe": "1h",
    }
    discovery = discovery_dataset(row)
    assert len(discovery.bars) == discovery.manifest.bar_count == 6
    assert discovery.bars[-1].close == 105.5
    assert discovery.manifest.requested_as_of == datetime(2026, 1, 1, 6, tzinfo=UTC)
    assert discovery.manifest.latest_bar_ts == datetime(2026, 1, 1, 5, tzinfo=UTC)
    assert discovery.manifest.backfill.bars_fetched == 6
    assert discovery.manifest.backfill.to_ts == discovery.manifest.requested_as_of
    assert discovery.manifest.content_sha256 != _dataset().manifest.content_sha256
    assert decode_frozen_dataset(row) == _dataset()
