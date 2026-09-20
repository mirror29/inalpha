"""Point-in-time tests for Data-to-Paper bar conversion."""

from datetime import UTC, datetime

from inalpha_paper.bar_conversion import bar_from_dict
from inalpha_paper.kernel.identifiers import InstrumentId


def _bar(ts: str) -> dict[str, object]:
    return {
        "ts": ts,
        "open": 100.0,
        "high": 110.0,
        "low": 90.0,
        "close": 105.0,
        "volume": 1_000.0,
    }


def test_monthly_bar_is_known_at_start_of_following_month() -> None:
    bar = bar_from_dict(
        _bar("2024-02-01T00:00:00Z"),
        InstrumentId("BTCUSDT", "BINANCE"),
        "1M",
    )

    expected = int(datetime(2024, 3, 1, tzinfo=UTC).timestamp() * 1_000_000_000)
    assert bar.bar_known_at == expected


def test_monthly_close_labeled_bar_cannot_be_known_before_next_month() -> None:
    bar = bar_from_dict(
        _bar("2024-02-29T00:00:00Z"),
        InstrumentId("600000", "SSE"),
        "1mo",
    )

    expected = int(datetime(2024, 3, 1, tzinfo=UTC).timestamp() * 1_000_000_000)
    assert bar.bar_known_at == expected
