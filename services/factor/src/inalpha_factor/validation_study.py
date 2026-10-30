"""Reproducible single-asset crypto time-series factor validation study (Issue #209).

Evaluates a predeclared set of existing ``PandasTAAdapter`` factors and a simple
rate-of-change baseline on frozen Binance spot ``BTC/USDT`` 1-hour OHLCV bars
across a development window and three non-overlapping out-of-sample (OOS)
quarterly windows.

Design guarantees:
- Strict UTC timestamp ordering, duplicate rejection, 1-hour boundary checks,
  and explicit missing-bar gap reporting without forward-filling future targets.
- Strictly causal factor computation via ``PandasTAAdapter`` with explicit
  warmup masking and contiguous-segment gap isolation so missing bars never
  fabricate post-gap signals.
- Window-bounded forward return targets ``close[t+h] / close[t] - 1`` where both
  ``t`` and ``t + h`` lie strictly inside ``[window_start, window_end)``, while
  preceding bars supply causal factor warmup.
- Explicit diagnostic flags exposing sentinel ``0.0`` returns in
  ``inalpha_factor.effectiveness`` (insufficient samples, constant factor/target,
  insufficient ICIR segments, zero segment dispersion).
- Predeclared causal regime sensitivity splits (24-bar realized volatility split
  at the frozen development median, and 24-bar trailing return sign).
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .adapters.pandas_ta_adapter import PandasTAAdapter
from .effectiveness import (
    _DECAY_STABLE_RETENTION,
    _IC_DIRECTION_THRESHOLD,
    _ICIR_SEGMENTS,
    EffResult,
    _forward_return,
    decay_state,
    ic_pvalue,
    null_ic_benchmark,
    score_factor,
)

REQUIRED_OHLCV_COLUMNS: tuple[str, ...] = ("open", "high", "low", "close", "volume")
HOUR_DELTA = pd.Timedelta(hours=1)


@dataclass(frozen=True, slots=True)
class StudyWindowSpec:
    """Predeclared evaluation window with inclusive start and exclusive end."""

    window_id: str
    label: str
    role: str  # "development", "oos", or "descriptive_full"
    start: str
    end: str
    expected_bars: int


@dataclass(frozen=True, slots=True)
class FactorStudySpec:
    """Predeclared factor specification for the Issue #209 validation study."""

    factor_id: str
    name: str
    role: str  # "core" or "baseline"
    category: str
    formula: str
    warmup_bars: int
    direction_hint: int | None


@dataclass(frozen=True, slots=True)
class StudyConfig:
    """Frozen pre-registered configuration for Issue #209."""

    study_id: str
    issue_number: int
    issue_url: str
    confirmation_comment_url: str
    instrument: str
    venue_symbol: str
    venue: str
    data_source_endpoint: str
    timeframe: str
    timestamp_convention: str
    frozen_start: str
    frozen_end: str
    expected_total_bars: int
    horizons: tuple[int, ...]
    quantiles: int
    min_samples: int
    ic_direction_threshold: float
    decay_stable_retention: float
    icir_segments: int
    null_benchmark_candidates: int
    regime_vol_lookback_bars: int
    regime_trend_lookback_bars: int
    windows: tuple[StudyWindowSpec, ...]
    descriptive_full_window: StudyWindowSpec
    factors: tuple[FactorStudySpec, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


STUDY_209_WINDOWS: tuple[StudyWindowSpec, ...] = (
    StudyWindowSpec(
        window_id="dev",
        label="Development (2025 H2)",
        role="development",
        start="2025-07-01T00:00:00Z",
        end="2026-01-01T00:00:00Z",
        expected_bars=4416,
    ),
    StudyWindowSpec(
        window_id="oos_2026_q1",
        label="OOS Q1 2026",
        role="oos",
        start="2026-01-01T00:00:00Z",
        end="2026-04-01T00:00:00Z",
        expected_bars=2160,
    ),
    StudyWindowSpec(
        window_id="oos_2026_q2",
        label="OOS Q2 2026",
        role="oos",
        start="2026-04-01T00:00:00Z",
        end="2026-07-01T00:00:00Z",
        expected_bars=2184,
    ),
    StudyWindowSpec(
        window_id="oos_2026_q3",
        label="OOS Q3 2026",
        role="oos",
        start="2026-07-01T00:00:00Z",
        end="2026-10-01T00:00:00Z",
        expected_bars=2208,
    ),
)

STUDY_209_FULL_WINDOW = StudyWindowSpec(
    window_id="full_period",
    label="Full Frozen Period (2025-07 to 2026-10)",
    role="descriptive_full",
    start="2025-07-01T00:00:00Z",
    end="2026-10-01T00:00:00Z",
    expected_bars=10968,
)

STUDY_209_FACTORS: tuple[FactorStudySpec, ...] = (
    FactorStudySpec(
        factor_id="pandas_ta.rsi_14",
        name="RSI(14)",
        role="core",
        category="mean_reversion",
        formula="100 - 100 / (1 + RMA(gain, 14) / RMA(loss, 14))",
        warmup_bars=14,
        direction_hint=-1,
    ),
    FactorStudySpec(
        factor_id="pandas_ta.macd_hist",
        name="MACD hist(12,26,9)/close",
        role="core",
        category="momentum",
        formula="((EMA(close, 12) - EMA(close, 26)) - EMA(MACD, 9)) / close",
        warmup_bars=33,
        direction_hint=1,
    ),
    FactorStudySpec(
        factor_id="pandas_ta.atr_pct_14",
        name="ATR(14)/close",
        role="core",
        category="volatility",
        formula="RMA(TrueRange, 14) / close",
        warmup_bars=14,
        direction_hint=None,
    ),
    FactorStudySpec(
        factor_id="pandas_ta.roc_10",
        name="ROC(10)",
        role="baseline",
        category="momentum",
        formula="(close[t] / close[t-10] - 1) * 100",
        warmup_bars=10,
        direction_hint=1,
    ),
)

FACTOR_SPEC_BY_ID: dict[str, FactorStudySpec] = {
    spec.factor_id: spec for spec in STUDY_209_FACTORS
}

STUDY_209_CONFIG = StudyConfig(
    study_id="issue_209_btc_1h_single_asset_validation",
    issue_number=209,
    issue_url="https://github.com/mirror29/inalpha/issues/209",
    confirmation_comment_url="https://github.com/mirror29/inalpha/issues/209#issuecomment-6513022958",
    instrument="BTC/USDT",
    venue_symbol="BTCUSDT",
    venue="binance_spot",
    data_source_endpoint="https://data-api.binance.vision/api/v3/klines",
    timeframe="1h",
    timestamp_convention="UTC bar-open timestamp; completed 1h bars only in [start, end)",
    frozen_start="2025-07-01T00:00:00Z",
    frozen_end="2026-10-01T00:00:00Z",
    expected_total_bars=10968,
    horizons=(1, 6, 24),
    quantiles=5,
    min_samples=120,
    ic_direction_threshold=_IC_DIRECTION_THRESHOLD,
    decay_stable_retention=_DECAY_STABLE_RETENTION,
    icir_segments=_ICIR_SEGMENTS,
    null_benchmark_candidates=len(STUDY_209_FACTORS),
    regime_vol_lookback_bars=24,
    regime_trend_lookback_bars=24,
    windows=STUDY_209_WINDOWS,
    descriptive_full_window=STUDY_209_FULL_WINDOW,
    factors=STUDY_209_FACTORS,
)


@dataclass(frozen=True, slots=True)
class SeriesAuditReport:
    """Data-hygiene and timestamp alignment audit result for an OHLCV dataset."""

    status: str  # "ok" or "invalid"
    row_count: int
    expected_bar_count: int
    first_timestamp: str | None
    last_timestamp: str | None
    is_utc: bool
    is_monotonic_increasing: bool
    is_hour_aligned: bool
    within_frozen_bounds: bool
    duplicate_count: int
    duplicate_timestamps: list[str]
    missing_bar_count: int
    missing_timestamps: list[str]
    non_finite_row_count: int
    non_positive_price_row_count: int
    invalid_ohlc_row_count: int
    negative_volume_row_count: int
    window_bar_counts: dict[str, int]
    errors: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _format_utc_iso(ts: pd.Timestamp) -> str:
    utc_ts = ts.tz_convert("UTC") if ts.tzinfo is not None else ts.tz_localize("UTC")
    return str(utc_ts.strftime("%Y-%m-%dT%H:%M:%SZ"))


def _parse_utc_timestamp(value: str | pd.Timestamp) -> pd.Timestamp:
    ts = pd.Timestamp(value)
    if ts.tzinfo is None:
        return ts.tz_localize("UTC")
    return ts.tz_convert("UTC")


def _extract_datetime_index(df: pd.DataFrame) -> tuple[pd.DatetimeIndex, bool]:
    """Return (DatetimeIndex, is_utc_tz_aware)."""
    if "timestamp" in df.columns:
        raw = df["timestamp"]
        if isinstance(raw.dtype, pd.DatetimeTZDtype):
            is_utc = str(raw.dtype.tz) == "UTC"
            idx = pd.DatetimeIndex(raw).tz_convert("UTC")
            return idx, is_utc
        parsed = pd.to_datetime(raw, utc=False)
        if isinstance(parsed.dtype, pd.DatetimeTZDtype):
            is_utc = str(parsed.dtype.tz) == "UTC"
            return pd.DatetimeIndex(parsed).tz_convert("UTC"), is_utc
        return pd.DatetimeIndex(parsed), False

    if isinstance(df.index, pd.DatetimeIndex):
        if df.index.tz is not None:
            is_utc = str(df.index.tz) == "UTC"
            return df.index.tz_convert("UTC"), is_utc
        return df.index, False

    raise ValueError("DataFrame must have a DatetimeIndex or a 'timestamp' column")


def load_ohlcv_csv(path: Path) -> pd.DataFrame:
    """Load frozen OHLCV CSV with explicit UTC timestamps as DatetimeIndex."""
    raw = pd.read_csv(path)
    if "timestamp" not in raw.columns:
        raise ValueError(f"Missing 'timestamp' column in {path}")
    missing_cols = [c for c in REQUIRED_OHLCV_COLUMNS if c not in raw.columns]
    if missing_cols:
        raise ValueError(f"Missing required OHLCV columns {missing_cols} in {path}")

    ts_raw = raw["timestamp"].astype(str)
    has_explicit_utc = bool(ts_raw.str.endswith(("Z", "+00:00", "+0000")).all())
    ts_index = pd.DatetimeIndex(pd.to_datetime(ts_raw, utc=True, format="ISO8601"))
    if not has_explicit_utc:
        raise ValueError("All timestamps in CSV must carry an explicit UTC offset ('Z' or '+00:00')")

    df = raw.loc[:, list(REQUIRED_OHLCV_COLUMNS)].copy()
    for col in REQUIRED_OHLCV_COLUMNS:
        df[col] = pd.to_numeric(df[col], errors="coerce").astype(float)
    df.index = ts_index
    df.index.name = "timestamp"
    return df


def audit_ohlcv_series(
    df: pd.DataFrame,
    *,
    expected_start: str | pd.Timestamp | None = None,
    expected_end: str | pd.Timestamp | None = None,
    windows: tuple[StudyWindowSpec, ...] = STUDY_209_WINDOWS,
) -> SeriesAuditReport:
    """Audit an OHLCV DataFrame for timestamp alignment, gaps, and data hygiene."""
    errors: list[str] = []
    missing_cols = [c for c in REQUIRED_OHLCV_COLUMNS if c not in df.columns]
    if missing_cols:
        raise ValueError(f"DataFrame missing required OHLCV columns: {missing_cols}")

    idx, is_utc = _extract_datetime_index(df)
    if not is_utc:
        errors.append("Timestamps must be timezone-aware UTC")

    row_count = len(df)
    if row_count == 0:
        errors.append("Dataset is empty")
        return SeriesAuditReport(
            status="invalid",
            row_count=0,
            expected_bar_count=0,
            first_timestamp=None,
            last_timestamp=None,
            is_utc=is_utc,
            is_monotonic_increasing=True,
            is_hour_aligned=True,
            within_frozen_bounds=False,
            duplicate_count=0,
            duplicate_timestamps=[],
            missing_bar_count=0,
            missing_timestamps=[],
            non_finite_row_count=0,
            non_positive_price_row_count=0,
            invalid_ohlc_row_count=0,
            negative_volume_row_count=0,
            window_bar_counts={w.window_id: 0 for w in windows},
            errors=errors,
        )

    utc_idx = idx.tz_convert("UTC") if idx.tz is not None else idx.tz_localize("UTC")
    is_monotonic = bool(utc_idx.is_monotonic_increasing)
    if not is_monotonic:
        errors.append("Timestamps are not in monotonically increasing order")

    dup_mask = utc_idx.duplicated(keep="first")
    duplicate_count = int(dup_mask.sum())
    duplicate_timestamps = [_format_utc_iso(ts) for ts in utc_idx[dup_mask][:25]]
    if duplicate_count > 0:
        errors.append(f"Found {duplicate_count} duplicate timestamp(s)")

    is_hour_aligned = bool(
        (utc_idx.minute == 0).all()
        and (utc_idx.second == 0).all()
        and (utc_idx.microsecond == 0).all()
        and (utc_idx.nanosecond == 0).all()
    )
    if not is_hour_aligned:
        errors.append("Timestamps are not aligned to exact 1-hour boundaries")

    grid_start = (
        _parse_utc_timestamp(expected_start)
        if expected_start is not None
        else utc_idx.min().floor("1h")
    )
    grid_end = (
        _parse_utc_timestamp(expected_end)
        if expected_end is not None
        else utc_idx.max().floor("1h") + HOUR_DELTA
    )

    within_bounds = bool((utc_idx >= grid_start).all() and (utc_idx < grid_end).all())
    if not within_bounds:
        errors.append(
            f"Timestamps fall outside expected interval [{_format_utc_iso(grid_start)}, {_format_utc_iso(grid_end)})"
        )

    expected_grid = pd.date_range(
        start=grid_start,
        end=grid_end - HOUR_DELTA,
        freq="1h",
        tz="UTC",
    )
    expected_bar_count = len(expected_grid)
    missing_idx = expected_grid.difference(utc_idx.unique())
    missing_bar_count = len(missing_idx)
    missing_timestamps = [_format_utc_iso(ts) for ts in missing_idx[:50]]

    ohlcv = df.loc[:, list(REQUIRED_OHLCV_COLUMNS)].astype(float)
    finite_mask = np.isfinite(ohlcv.to_numpy()).all(axis=1)
    non_finite_row_count = int((~finite_mask).sum())
    if non_finite_row_count > 0:
        errors.append(f"Found {non_finite_row_count} row(s) with NaN or infinite OHLCV values")

    price_cols = ohlcv.loc[:, ["open", "high", "low", "close"]]
    positive_mask = (price_cols > 0.0).all(axis=1) & finite_mask
    non_positive_price_row_count = int((~positive_mask & finite_mask).sum())
    if non_positive_price_row_count > 0:
        errors.append(f"Found {non_positive_price_row_count} row(s) with non-positive prices")

    ohlc_valid_mask = (
        (ohlcv["high"] >= ohlcv[["open", "close", "low"]].max(axis=1))
        & (ohlcv["low"] <= ohlcv[["open", "close", "high"]].min(axis=1))
        & finite_mask
    )
    invalid_ohlc_row_count = int((~ohlc_valid_mask & finite_mask).sum())
    if invalid_ohlc_row_count > 0:
        errors.append(f"Found {invalid_ohlc_row_count} row(s) violating high/low envelope")

    neg_vol_mask = (ohlcv["volume"] < 0.0) & finite_mask
    negative_volume_row_count = int(neg_vol_mask.sum())
    if negative_volume_row_count > 0:
        errors.append(f"Found {negative_volume_row_count} row(s) with negative volume")

    window_bar_counts: dict[str, int] = {}
    for w in windows:
        w_start = _parse_utc_timestamp(w.start)
        w_end = _parse_utc_timestamp(w.end)
        in_win = (utc_idx >= w_start) & (utc_idx < w_end)
        window_bar_counts[w.window_id] = int(in_win.sum())

    status = "ok" if not errors else "invalid"
    return SeriesAuditReport(
        status=status,
        row_count=row_count,
        expected_bar_count=expected_bar_count,
        first_timestamp=_format_utc_iso(utc_idx[0]),
        last_timestamp=_format_utc_iso(utc_idx[-1]),
        is_utc=is_utc,
        is_monotonic_increasing=is_monotonic,
        is_hour_aligned=is_hour_aligned,
        within_frozen_bounds=within_bounds,
        duplicate_count=duplicate_count,
        duplicate_timestamps=duplicate_timestamps,
        missing_bar_count=missing_bar_count,
        missing_timestamps=missing_timestamps,
        non_finite_row_count=non_finite_row_count,
        non_positive_price_row_count=non_positive_price_row_count,
        invalid_ohlc_row_count=invalid_ohlc_row_count,
        negative_volume_row_count=negative_volume_row_count,
        window_bar_counts=window_bar_counts,
        errors=errors,
    )


def reindex_to_hourly_grid(
    df: pd.DataFrame,
    *,
    start: str | pd.Timestamp | None = None,
    end: str | pd.Timestamp | None = None,
) -> pd.DataFrame:
    """Validate and reindex an OHLCV DataFrame onto a complete 1h UTC grid.

    Missing bars are represented as ``NaN`` rows and are never forward-filled.
    Raises ``ValueError`` if timestamps are non-UTC, unsorted, duplicated,
    misaligned, or contain corrupt OHLCV values.
    """
    audit = audit_ohlcv_series(df, expected_start=start, expected_end=end)
    if audit.status != "ok":
        raise ValueError(f"OHLCV audit failed: {'; '.join(audit.errors)}")

    idx, _ = _extract_datetime_index(df)
    utc_idx = idx.tz_convert("UTC")
    clean = df.loc[:, list(REQUIRED_OHLCV_COLUMNS)].astype(float).copy()
    clean.index = utc_idx
    clean.index.name = "timestamp"

    grid_start = _parse_utc_timestamp(start) if start is not None else utc_idx[0]
    grid_end = _parse_utc_timestamp(end) if end is not None else utc_idx[-1] + HOUR_DELTA
    full_grid = pd.date_range(
        start=grid_start,
        end=grid_end - HOUR_DELTA,
        freq="1h",
        tz="UTC",
        name="timestamp",
    )
    return clean.reindex(full_grid)


def compute_study_factors(
    df_on_grid: pd.DataFrame,
    factor_ids: list[str] | None = None,
    *,
    enforce_warmup: bool = True,
) -> dict[str, pd.Series]:
    """Compute predeclared study factors causally on an hourly OHLCV grid.

    Each contiguous gap-free segment of valid OHLCV bars is passed to
    :class:`PandasTAAdapter` independently so missing bars never allow
    ``pct_change`` padding or recursive EMA/RMA state to silently bridge across
    an unrecorded gap. Within each contiguous segment, the leading
    ``warmup_bars`` observations are set to ``NaN`` until enough history exists.
    """
    target_ids = (
        list(factor_ids)
        if factor_ids is not None
        else [spec.factor_id for spec in STUDY_209_FACTORS]
    )
    adapter = PandasTAAdapter()
    out: dict[str, pd.Series] = {
        fid: pd.Series(np.nan, index=df_on_grid.index, dtype=float, name=fid)
        for fid in target_ids
    }

    valid_row_mask = np.isfinite(
        df_on_grid.loc[:, list(REQUIRED_OHLCV_COLUMNS)].to_numpy()
    ).all(axis=1)
    if not valid_row_mask.any():
        return out

    # Split into contiguous valid-bar slices [seg_start, seg_end)
    n = len(df_on_grid)
    seg_start: int | None = None
    segments: list[tuple[int, int]] = []
    for idx_pos in range(n):
        if valid_row_mask[idx_pos]:
            if seg_start is None:
                seg_start = idx_pos
        elif seg_start is not None:
            segments.append((seg_start, idx_pos))
            seg_start = None
    if seg_start is not None:
        segments.append((seg_start, n))

    for start_pos, end_pos in segments:
        seg_df = df_on_grid.iloc[start_pos:end_pos]
        computed = adapter.compute(seg_df, factor_ids=target_ids)
        for fid in target_ids:
            series = computed[fid].astype(float).copy()
            if enforce_warmup and fid in FACTOR_SPEC_BY_ID:
                warmup = FACTOR_SPEC_BY_ID[fid].warmup_bars
                if warmup > 0:
                    series.iloc[: min(warmup, len(series))] = np.nan
            out[fid].iloc[start_pos:end_pos] = series

    return out


def compute_window_forward_return(
    close_on_grid: pd.Series,
    *,
    window_start: str | pd.Timestamp,
    window_end: str | pd.Timestamp,
    horizon: int,
) -> pd.Series:
    """Compute window-bounded forward returns ``close[t+h] / close[t] - 1``.

    Slicing ``close_on_grid`` to ``[window_start, window_end)`` before calling
    :func:`_forward_return` ensures that the trailing ``horizon`` bars of the
    window receive ``NaN`` targets rather than reading prices from the next
    window. Because ``close_on_grid`` resides on the complete 1-hour UTC grid
    with ``NaN`` at missing bars, positional ``shift(-horizon)`` aligns strictly
    with timestamp ``t + horizon * 1h`` and never forward-fills across gaps.
    """
    if horizon < 1:
        raise ValueError(f"horizon must be >= 1, got {horizon}")
    w_start = _parse_utc_timestamp(window_start)
    w_end = _parse_utc_timestamp(window_end)
    mask = (close_on_grid.index >= w_start) & (close_on_grid.index < w_end)
    window_close = close_on_grid.loc[mask]
    return _forward_return(window_close, horizon)


@dataclass(frozen=True, slots=True)
class PairDiagnostics:
    """Explicit diagnostics exposing sentinel 0.0 cases in effectiveness.py."""

    status: str
    window_bar_count: int
    valid_factor_count: int
    warmup_or_gap_nan_count: int
    horizon_tail_nan_count: int
    sample_size: int
    factor_std: float | None
    factor_rank_std: float | None
    target_std: float | None
    target_rank_std: float | None
    segment_ics: list[float]
    valid_icir_segments: int
    sentinel_zero_flags: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def inspect_pair_diagnostics(
    window_factor: pd.Series,
    window_fwd: pd.Series,
    *,
    horizon: int,
    segments: int = _ICIR_SEGMENTS,
) -> PairDiagnostics:
    """Inspect aligned (factor, forward_return) pairs and expose sentinel zeros."""
    f_clean = window_factor.replace([np.inf, -np.inf], np.nan)
    r_clean = window_fwd.replace([np.inf, -np.inf], np.nan)
    window_bar_count = len(f_clean)
    valid_factor_count = int(f_clean.notna().sum())
    warmup_or_gap_nan_count = int(f_clean.isna().sum())
    horizon_tail_nan_count = min(horizon, window_bar_count)

    pair = pd.concat([f_clean.rename("f"), r_clean.rename("r")], axis=1).dropna()
    n = len(pair)
    sentinel_flags: list[str] = []

    if n < 3:
        sentinel_flags.append("sample_size_lt_3_rank_ic_sentinel_zero")
        sentinel_flags.append("sample_size_lt_15_icir_sentinel_zero")
        sentinel_flags.append("recent_window_lt_3_recent_ic_sentinel_zero")
        return PairDiagnostics(
            status="insufficient_samples",
            window_bar_count=window_bar_count,
            valid_factor_count=valid_factor_count,
            warmup_or_gap_nan_count=warmup_or_gap_nan_count,
            horizon_tail_nan_count=horizon_tail_nan_count,
            sample_size=n,
            factor_std=None,
            factor_rank_std=None,
            target_std=None,
            target_rank_std=None,
            segment_ics=[],
            valid_icir_segments=0,
            sentinel_zero_flags=sentinel_flags,
        )

    f_std = float(pair["f"].std(ddof=0))
    r_std = float(pair["r"].std(ddof=0))
    fr = pair["f"].rank()
    rr = pair["r"].rank()
    fr_std = float(fr.std(ddof=0))
    rr_std = float(rr.std(ddof=0))

    if fr_std == 0.0:
        sentinel_flags.append("constant_factor_rank_ic_sentinel_zero")
    if rr_std == 0.0:
        sentinel_flags.append("constant_target_rank_ic_sentinel_zero")

    tail = n // 3
    if tail < 3:
        sentinel_flags.append("recent_window_lt_3_recent_ic_sentinel_zero")
    else:
        ch_tail = pair.iloc[-tail:]
        if (
            ch_tail["f"].rank().std(ddof=0) == 0.0
            or ch_tail["r"].rank().std(ddof=0) == 0.0
        ):
            sentinel_flags.append("constant_recent_window_recent_ic_sentinel_zero")

    segment_ics: list[float] = []
    if n < segments * 3:
        sentinel_flags.append("sample_size_lt_15_icir_sentinel_zero")
    else:
        bounds = np.linspace(0, n, segments + 1, dtype=int)
        for i in range(segments):
            ch = pair.iloc[bounds[i] : bounds[i + 1]]
            if len(ch) < 3:
                continue
            ch_fr = ch["f"].rank()
            ch_rr = ch["r"].rank()
            if ch_fr.std(ddof=0) == 0.0 or ch_rr.std(ddof=0) == 0.0:
                continue
            seg_ic = float(ch_fr.corr(ch_rr))
            if not np.isnan(seg_ic):
                segment_ics.append(seg_ic)
        if len(segment_ics) < 2:
            sentinel_flags.append("fewer_than_2_valid_segments_icir_sentinel_zero")
        elif float(np.std(segment_ics, ddof=1)) == 0.0:
            sentinel_flags.append("zero_segment_ic_std_icir_sentinel_zero")

    if fr_std == 0.0:
        status = "constant_factor"
    elif rr_std == 0.0:
        status = "constant_target"
    elif n < segments * 3 or len(segment_ics) < 2:
        status = "insufficient_icir_segments"
    elif float(np.std(segment_ics, ddof=1)) == 0.0:
        status = "zero_icir_std"
    else:
        status = "ok"

    return PairDiagnostics(
        status=status,
        window_bar_count=window_bar_count,
        valid_factor_count=valid_factor_count,
        warmup_or_gap_nan_count=warmup_or_gap_nan_count,
        horizon_tail_nan_count=horizon_tail_nan_count,
        sample_size=n,
        factor_std=f_std,
        factor_rank_std=fr_std,
        target_std=r_std,
        target_rank_std=rr_std,
        segment_ics=segment_ics,
        valid_icir_segments=len(segment_ics),
        sentinel_zero_flags=sentinel_flags,
    )


def evaluate_window_factor(
    factor_on_grid: pd.Series,
    close_on_grid: pd.Series,
    *,
    window: StudyWindowSpec,
    factor_spec: FactorStudySpec,
    horizon: int,
    quantiles: int = 5,
    min_samples: int = 120,
    n_candidates: int = 4,
) -> dict[str, Any]:
    """Evaluate a single (window, factor, horizon) cell using score_factor."""
    w_start = _parse_utc_timestamp(window.start)
    w_end = _parse_utc_timestamp(window.end)
    mask = (close_on_grid.index >= w_start) & (close_on_grid.index < w_end)
    w_factor = factor_on_grid.loc[mask]
    w_close = close_on_grid.loc[mask]
    w_fwd = _forward_return(w_close, horizon)

    eff: EffResult = score_factor(
        w_factor,
        w_close,
        horizon=horizon,
        quantiles=quantiles,
        min_samples=min_samples,
    )
    diag = inspect_pair_diagnostics(w_factor, w_fwd, horizon=horizon)

    null_n4 = null_ic_benchmark(n_candidates, eff.sample_size, horizon)
    null_n1 = null_ic_benchmark(1, eff.sample_size, horizon)
    pval = ic_pvalue(eff.rank_ic, eff.sample_size, horizon) if eff.sample_size >= 4 else 1.0

    within_retention: float | None = None
    if eff.rank_ic != 0.0:
        within_retention = float(eff.rank_ic_recent / eff.rank_ic)

    return {
        "window_id": window.window_id,
        "window_label": window.label,
        "window_role": window.role,
        "window_start": window.start,
        "window_end": window.end,
        "factor_id": factor_spec.factor_id,
        "factor_name": factor_spec.name,
        "factor_role": factor_spec.role,
        "category": factor_spec.category,
        "direction_hint": factor_spec.direction_hint,
        "warmup_bars": factor_spec.warmup_bars,
        "horizon": horizon,
        "status": diag.status,
        "sentinel_zero_flags": diag.sentinel_zero_flags,
        "window_bar_count": diag.window_bar_count,
        "valid_factor_count": diag.valid_factor_count,
        "warmup_or_gap_nan_count": diag.warmup_or_gap_nan_count,
        "horizon_tail_nan_count": diag.horizon_tail_nan_count,
        "sample_size": eff.sample_size,
        "low_confidence": eff.low_confidence,
        "rank_ic": eff.rank_ic,
        "icir": eff.icir,
        "rank_ic_recent": eff.rank_ic_recent,
        "within_window_retention_ratio": within_retention,
        "decay_state": eff.decay_state,
        "direction": eff.direction,
        "strength": eff.strength,
        "turnover": eff.turnover,
        "long_short_return": eff.long_short_return,
        "quantile_returns": [
            {"quantile": q, "mean_return": m, "count": cnt}
            for q, m, cnt in eff.quantile_returns
        ],
        "segment_ics": diag.segment_ics,
        "valid_icir_segments": diag.valid_icir_segments,
        "null_ic_n4": null_n4,
        "null_ic_n1": null_n1,
        "exceeds_null_n4": bool(abs(eff.rank_ic) > null_n4 and diag.status == "ok"),
        "exceeds_null_n1": bool(abs(eff.rank_ic) > null_n1 and diag.status == "ok"),
        "ic_pvalue_approx": pval,
        "passes_direction_threshold": bool(
            not eff.low_confidence
            and abs(eff.rank_ic) >= _IC_DIRECTION_THRESHOLD
            and diag.status == "ok"
        ),
        "passes_within_window_stability": bool(
            eff.decay_state == "stable" and diag.status == "ok"
        ),
    }


def compute_regime_labels(
    close_on_grid: pd.Series,
    *,
    dev_start: str | pd.Timestamp = "2025-07-01T00:00:00Z",
    dev_end: str | pd.Timestamp = "2026-01-01T00:00:00Z",
    vol_lookback: int = 24,
    trend_lookback: int = 24,
) -> tuple[pd.DataFrame, float]:
    """Compute strictly causal volatility and trend regime labels at bar t.

    - ``ret_1h[t] = close[t] / close[t-1] - 1`` (no padding across gaps).
    - ``vol_24[t]`` is the 24-bar sample standard deviation of ``ret_1h`` over
      ``[t-23, ..., t]`` requiring all 24 bars to be present.
    - The volatility split threshold is frozen as the median of valid ``vol_24``
      strictly within the development window ``[dev_start, dev_end)``.
    - ``ret_24[t] = close[t] / close[t-24] - 1`` classifies ``uptrend``
      (``> 0``) vs ``downtrend`` (``<= 0``) using only past bars.
    """
    ret_1h = close_on_grid / close_on_grid.shift(1) - 1.0
    vol_24 = ret_1h.rolling(vol_lookback, min_periods=vol_lookback).std(ddof=1)
    ret_24 = close_on_grid / close_on_grid.shift(trend_lookback) - 1.0

    d_start = _parse_utc_timestamp(dev_start)
    d_end = _parse_utc_timestamp(dev_end)
    dev_vol = vol_24.loc[(vol_24.index >= d_start) & (vol_24.index < d_end)].dropna()
    if len(dev_vol) == 0:
        raise ValueError("Cannot freeze volatility regime threshold: no valid dev_vol samples")
    frozen_vol_median = float(dev_vol.median())

    vol_regime = pd.Series(index=close_on_grid.index, dtype="object")
    vol_regime.loc[vol_24.notna() & (vol_24 <= frozen_vol_median)] = "low_vol"
    vol_regime.loc[vol_24.notna() & (vol_24 > frozen_vol_median)] = "high_vol"

    trend_regime = pd.Series(index=close_on_grid.index, dtype="object")
    trend_regime.loc[ret_24.notna() & (ret_24 > 0.0)] = "uptrend"
    trend_regime.loc[ret_24.notna() & (ret_24 <= 0.0)] = "downtrend"

    regimes = pd.DataFrame(
        {
            "vol_24": vol_24,
            "vol_regime": vol_regime,
            "ret_24": ret_24,
            "trend_regime": trend_regime,
        },
        index=close_on_grid.index,
    )
    return regimes, frozen_vol_median


def _evaluate_subset_pair(
    sub_factor: pd.Series,
    sub_fwd: pd.Series,
    *,
    horizon: int,
    min_samples: int = 120,
    n_candidates: int = 4,
) -> dict[str, Any]:
    """Evaluate Rank IC and ICIR on a regime-filtered subset of (factor, fwd) pairs."""
    diag = inspect_pair_diagnostics(sub_factor, sub_fwd, horizon=horizon)
    pair = (
        pd.concat([sub_factor.rename("f"), sub_fwd.rename("r")], axis=1)
        .replace([np.inf, -np.inf], np.nan)
        .dropna()
    )
    n = len(pair)
    if n < 3 or diag.status in ("constant_factor", "constant_target"):
        rank_ic = 0.0
    else:
        rank_ic = float(pair["f"].rank().corr(pair["r"].rank()))
        if np.isnan(rank_ic):
            rank_ic = 0.0

    if len(diag.segment_ics) >= 2 and float(np.std(diag.segment_ics, ddof=1)) > 0.0:
        icir = float(np.mean(diag.segment_ics) / np.std(diag.segment_ics, ddof=1))
    else:
        icir = 0.0

    null_n4 = null_ic_benchmark(n_candidates, n, horizon)
    return {
        "status": diag.status,
        "sample_size": n,
        "low_confidence": bool(n < min_samples),
        "rank_ic": rank_ic,
        "icir": icir,
        "null_ic_n4": null_n4,
        "exceeds_null_n4": bool(abs(rank_ic) > null_n4 and diag.status == "ok"),
        "sentinel_zero_flags": diag.sentinel_zero_flags,
    }


def run_validation_study(
    df: pd.DataFrame,
    *,
    config: StudyConfig = STUDY_209_CONFIG,
) -> dict[str, Any]:
    """Execute the full Issue #209 validation study on the provided OHLCV dataset."""
    audit = audit_ohlcv_series(
        df,
        expected_start=config.frozen_start,
        expected_end=config.frozen_end,
        windows=config.windows,
    )
    if audit.status != "ok":
        raise ValueError(f"Dataset failed hygiene audit: {'; '.join(audit.errors)}")

    df_grid = reindex_to_hourly_grid(
        df,
        start=config.frozen_start,
        end=config.frozen_end,
    )
    close_grid = df_grid["close"]
    factors_grid = compute_study_factors(
        df_grid,
        [f.factor_id for f in config.factors],
        enforce_warmup=True,
    )

    all_windows: tuple[StudyWindowSpec, ...] = (
        *config.windows,
        config.descriptive_full_window,
    )

    # 1. Evaluate every (window, factor, horizon) cell
    raw_cells: dict[tuple[str, str, int], dict[str, Any]] = {}
    for window in all_windows:
        for f_spec in config.factors:
            for horizon in config.horizons:
                cell = evaluate_window_factor(
                    factors_grid[f_spec.factor_id],
                    close_grid,
                    window=window,
                    factor_spec=f_spec,
                    horizon=horizon,
                    quantiles=config.quantiles,
                    min_samples=config.min_samples,
                    n_candidates=config.null_benchmark_candidates,
                )
                raw_cells[(window.window_id, f_spec.factor_id, horizon)] = cell

    # 2. Enrich cells with development reference & baseline comparisons
    primary_rows: list[dict[str, Any]] = []
    descriptive_full_rows: list[dict[str, Any]] = []

    for window in all_windows:
        for f_spec in config.factors:
            for horizon in config.horizons:
                cell = dict(raw_cells[(window.window_id, f_spec.factor_id, horizon)])
                dev_cell = raw_cells[("dev", f_spec.factor_id, horizon)]
                base_cell = raw_cells[(window.window_id, "pandas_ta.roc_10", horizon)]

                dev_ic = float(dev_cell["rank_ic"])
                dev_dir = int(dev_cell["direction"])
                cur_ic = float(cell["rank_ic"])
                base_ic = float(base_cell["rank_ic"])

                ic_diff_vs_dev = cur_ic - dev_ic
                oos_retention_vs_dev: float | None = None
                oos_degradation_vs_dev: float | None = None
                if dev_ic != 0.0:
                    oos_retention_vs_dev = float(cur_ic / dev_ic)
                    oos_degradation_vs_dev = float(1.0 - (cur_ic / dev_ic))

                sign_preserved = bool(
                    cur_ic != 0.0
                    and dev_ic != 0.0
                    and np.sign(cur_ic) == np.sign(dev_ic)
                )
                direction_preserved = bool(
                    dev_dir != 0 and int(cell["direction"]) == dev_dir
                )
                oos_vs_dev_decay = decay_state(dev_ic, cur_ic)

                cell.update(
                    {
                        "dev_rank_ic": dev_ic,
                        "dev_icir": float(dev_cell["icir"]),
                        "dev_direction": dev_dir,
                        "ic_diff_vs_dev": ic_diff_vs_dev,
                        "oos_retention_vs_dev": oos_retention_vs_dev,
                        "oos_degradation_vs_dev": oos_degradation_vs_dev,
                        "sign_preserved_vs_dev": sign_preserved,
                        "direction_preserved_vs_dev": direction_preserved,
                        "oos_vs_dev_decay_state": oos_vs_dev_decay,
                        "baseline_rank_ic": base_ic,
                        "abs_ic_vs_baseline": abs(cur_ic) - abs(base_ic),
                        "beats_baseline_abs_ic": bool(abs(cur_ic) > abs(base_ic)),
                    }
                )
                if window.role == "descriptive_full":
                    descriptive_full_rows.append(cell)
                else:
                    primary_rows.append(cell)

    # 3. Build per-(factor, horizon) cross-window stability summary
    oos_window_ids = [w.window_id for w in config.windows if w.role == "oos"]
    factor_horizon_summaries: list[dict[str, Any]] = []
    for f_spec in config.factors:
        for horizon in config.horizons:
            dev_cell = raw_cells[("dev", f_spec.factor_id, horizon)]
            full_cell = raw_cells[("full_period", f_spec.factor_id, horizon)]
            oos_cells = [
                raw_cells[(wid, f_spec.factor_id, horizon)] for wid in oos_window_ids
            ]
            oos_ics = [float(c["rank_ic"]) for c in oos_cells]
            dev_ic = float(dev_cell["rank_ic"])
            dev_dir = int(dev_cell["direction"])

            oos_mean = float(np.mean(oos_ics))
            oos_std = float(np.std(oos_ics, ddof=1)) if len(oos_ics) > 1 else 0.0
            mean_retention = float(oos_mean / dev_ic) if dev_ic != 0.0 else None
            mean_degradation = (
                float(1.0 - (oos_mean / dev_ic)) if dev_ic != 0.0 else None
            )

            sign_match_count = sum(
                1
                for ic in oos_ics
                if ic != 0.0 and dev_ic != 0.0 and np.sign(ic) == np.sign(dev_ic)
            )
            dir_match_count = sum(
                1 for c in oos_cells if dev_dir != 0 and int(c["direction"]) == dev_dir
            )
            stable_vs_dev_count = sum(
                1 for ic in oos_ics if decay_state(dev_ic, ic) == "stable"
            )
            exceeds_null_n4_count = sum(
                1 for c in oos_cells if bool(c["exceeds_null_n4"])
            )
            beats_baseline_oos_count = sum(
                1
                for wid, c in zip(oos_window_ids, oos_cells, strict=True)
                if abs(float(c["rank_ic"]))
                > abs(float(raw_cells[(wid, "pandas_ta.roc_10", horizon)]["rank_ic"]))
            )

            # Classify overall validation finding for this (factor, horizon)
            if abs(dev_ic) < _IC_DIRECTION_THRESHOLD:
                verdict = "weak_in_sample"
            elif stable_vs_dev_count == len(oos_window_ids) and exceeds_null_n4_count >= 2:
                verdict = "stable_across_oos"
            elif sign_match_count >= 2 and stable_vs_dev_count >= 2:
                verdict = "partially_stable_regime_dependent"
            elif sign_match_count <= 1:
                verdict = "unstable_sign_flip_or_decay"
            else:
                verdict = "fading_in_oos"

            factor_horizon_summaries.append(
                {
                    "factor_id": f_spec.factor_id,
                    "factor_name": f_spec.name,
                    "factor_role": f_spec.role,
                    "category": f_spec.category,
                    "direction_hint": f_spec.direction_hint,
                    "horizon": horizon,
                    "dev_rank_ic": dev_ic,
                    "dev_icir": float(dev_cell["icir"]),
                    "dev_direction": dev_dir,
                    "dev_within_decay_state": str(dev_cell["decay_state"]),
                    "dev_exceeds_null_n4": bool(dev_cell["exceeds_null_n4"]),
                    "oos_q1_rank_ic": float(oos_cells[0]["rank_ic"]),
                    "oos_q2_rank_ic": float(oos_cells[1]["rank_ic"]),
                    "oos_q3_rank_ic": float(oos_cells[2]["rank_ic"]),
                    "oos_mean_rank_ic": oos_mean,
                    "oos_std_rank_ic": oos_std,
                    "oos_min_rank_ic": float(min(oos_ics)),
                    "oos_max_rank_ic": float(max(oos_ics)),
                    "oos_mean_retention_vs_dev": mean_retention,
                    "oos_mean_degradation_vs_dev": mean_degradation,
                    "oos_sign_consistency_count": sign_match_count,
                    "oos_direction_consistency_count": dir_match_count,
                    "oos_stable_vs_dev_count": stable_vs_dev_count,
                    "oos_exceeds_null_n4_count": exceeds_null_n4_count,
                    "oos_beats_baseline_count": beats_baseline_oos_count,
                    "full_period_rank_ic": float(full_cell["rank_ic"]),
                    "full_period_icir": float(full_cell["icir"]),
                    "full_period_recent_ic": float(full_cell["rank_ic_recent"]),
                    "full_period_decay_state": str(full_cell["decay_state"]),
                    "validation_verdict": verdict,
                }
            )

    # 4. Regime sensitivity evaluation (strictly causal, dev-frozen volatility median)
    regimes_df, frozen_vol_median = compute_regime_labels(
        close_grid,
        dev_start=config.windows[0].start,
        dev_end=config.windows[0].end,
        vol_lookback=config.regime_vol_lookback_bars,
        trend_lookback=config.regime_trend_lookback_bars,
    )

    # Window-bounded forward returns per horizon so pooled OOS never crosses window boundaries
    bounded_fwd_by_window: dict[tuple[str, int], pd.Series] = {}
    for w in config.windows:
        for h in config.horizons:
            bounded_fwd_by_window[(w.window_id, h)] = compute_window_forward_return(
                close_grid,
                window_start=w.start,
                window_end=w.end,
                horizon=h,
            )

    regime_rows: list[dict[str, Any]] = []
    regime_definitions: list[tuple[str, str, str]] = [
        ("volatility", "vol_regime", "low_vol"),
        ("volatility", "vol_regime", "high_vol"),
        ("trend", "trend_regime", "uptrend"),
        ("trend", "trend_regime", "downtrend"),
    ]

    for f_spec in config.factors:
        f_series = factors_grid[f_spec.factor_id]
        for horizon in config.horizons:
            # Evaluate on Development and Pooled OOS (concatenating window-bounded OOS targets)
            dev_fwd = bounded_fwd_by_window[("dev", horizon)]
            oos_fwd = pd.concat(
                [bounded_fwd_by_window[(wid, horizon)] for wid in oos_window_ids]
            )
            scope_map: list[tuple[str, pd.Series]] = [
                ("dev", dev_fwd),
                ("oos_pooled", oos_fwd),
                *[
                    (wid, bounded_fwd_by_window[(wid, horizon)])
                    for wid in oos_window_ids
                ],
            ]
            for scope_id, scope_fwd in scope_map:
                scope_regimes = regimes_df.loc[scope_fwd.index]
                scope_factor = f_series.loc[scope_fwd.index]
                for dimension, col_name, regime_label in regime_definitions:
                    reg_mask = scope_regimes[col_name] == regime_label
                    metrics = _evaluate_subset_pair(
                        scope_factor.loc[reg_mask],
                        scope_fwd.loc[reg_mask],
                        horizon=horizon,
                        min_samples=config.min_samples,
                        n_candidates=config.null_benchmark_candidates,
                    )
                    regime_rows.append(
                        {
                            "scope": scope_id,
                            "factor_id": f_spec.factor_id,
                            "factor_name": f_spec.name,
                            "factor_role": f_spec.role,
                            "horizon": horizon,
                            "regime_dimension": dimension,
                            "regime_label": regime_label,
                            **metrics,
                        }
                    )

    # Window regime occupancy statistics
    regime_occupancy: dict[str, dict[str, Any]] = {}
    for w in config.windows:
        w_start = _parse_utc_timestamp(w.start)
        w_end = _parse_utc_timestamp(w.end)
        sub_reg = regimes_df.loc[(regimes_df.index >= w_start) & (regimes_df.index < w_end)]
        sub_close = close_grid.loc[sub_reg.index]
        period_return = float(sub_close.iloc[-1] / sub_close.iloc[0] - 1.0)
        vol_counts = sub_reg["vol_regime"].value_counts(dropna=True).to_dict()
        trend_counts = sub_reg["trend_regime"].value_counts(dropna=True).to_dict()
        vol_total = sum(int(v) for v in vol_counts.values())
        trend_total = sum(int(v) for v in trend_counts.values())
        regime_occupancy[w.window_id] = {
            "window_label": w.label,
            "start_close": float(sub_close.iloc[0]),
            "end_close": float(sub_close.iloc[-1]),
            "period_return": period_return,
            "low_vol_bars": int(vol_counts.get("low_vol", 0)),
            "high_vol_bars": int(vol_counts.get("high_vol", 0)),
            "high_vol_share": (
                float(int(vol_counts.get("high_vol", 0)) / vol_total)
                if vol_total > 0
                else 0.0
            ),
            "uptrend_bars": int(trend_counts.get("uptrend", 0)),
            "downtrend_bars": int(trend_counts.get("downtrend", 0)),
            "uptrend_share": (
                float(int(trend_counts.get("uptrend", 0)) / trend_total)
                if trend_total > 0
                else 0.0
            ),
        }

    return {
        "study_id": config.study_id,
        "issue_number": config.issue_number,
        "audit": audit.to_dict(),
        "regime_thresholds": {
            "vol_24_dev_median": frozen_vol_median,
            "vol_lookback_bars": config.regime_vol_lookback_bars,
            "trend_lookback_bars": config.regime_trend_lookback_bars,
        },
        "regime_occupancy": regime_occupancy,
        "primary_results": primary_rows,
        "descriptive_full_results": descriptive_full_rows,
        "factor_horizon_summaries": factor_horizon_summaries,
        "regime_results": regime_rows,
    }


def compute_file_sha256(path: Path) -> str:
    """Compute hex SHA-256 digest of a file's raw bytes."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def build_dataset_manifest(
    csv_path: Path,
    df: pd.DataFrame,
    *,
    config: StudyConfig = STUDY_209_CONFIG,
    extracted_at_utc: str = "2026-10-30T01:48:00Z",
    git_revision: str = "230d67da5b869842a25ed30c9849e01c9b583c27",
) -> dict[str, Any]:
    """Build deterministic dataset manifest for the frozen OHLCV CSV."""
    audit = audit_ohlcv_series(
        df,
        expected_start=config.frozen_start,
        expected_end=config.frozen_end,
        windows=config.windows,
    )
    return {
        "study_id": config.study_id,
        "issue_number": config.issue_number,
        "dataset_file": csv_path.name,
        "dataset_sha256": compute_file_sha256(csv_path),
        "dataset_size_bytes": csv_path.stat().st_size,
        "instrument": config.instrument,
        "venue_symbol": config.venue_symbol,
        "venue": config.venue,
        "source_endpoint": config.data_source_endpoint,
        "timeframe": config.timeframe,
        "timestamp_convention": config.timestamp_convention,
        "extracted_at_utc": extracted_at_utc,
        "software_git_revision": git_revision,
        "frozen_interval": {
            "start": config.frozen_start,
            "end": config.frozen_end,
            "start_inclusive": True,
            "end_exclusive": True,
        },
        "coverage": {
            "row_count": audit.row_count,
            "expected_bar_count": audit.expected_bar_count,
            "first_timestamp": audit.first_timestamp,
            "last_timestamp": audit.last_timestamp,
            "missing_bar_count": audit.missing_bar_count,
            "missing_timestamps": audit.missing_timestamps,
            "duplicate_count": audit.duplicate_count,
            "duplicate_timestamps": audit.duplicate_timestamps,
            "window_bar_counts": audit.window_bar_counts,
        },
        "hygiene_checks": {
            "status": audit.status,
            "is_utc": audit.is_utc,
            "is_monotonic_increasing": audit.is_monotonic_increasing,
            "is_hour_aligned": audit.is_hour_aligned,
            "within_frozen_bounds": audit.within_frozen_bounds,
            "non_finite_row_count": audit.non_finite_row_count,
            "non_positive_price_row_count": audit.non_positive_price_row_count,
            "invalid_ohlc_row_count": audit.invalid_ohlc_row_count,
            "negative_volume_row_count": audit.negative_volume_row_count,
            "errors": audit.errors,
        },
    }


RESULTS_CSV_COLUMNS: tuple[str, ...] = (
    "window_id",
    "window_role",
    "window_start",
    "window_end",
    "factor_id",
    "factor_role",
    "category",
    "direction_hint",
    "warmup_bars",
    "horizon",
    "status",
    "window_bar_count",
    "valid_factor_count",
    "warmup_or_gap_nan_count",
    "horizon_tail_nan_count",
    "sample_size",
    "low_confidence",
    "rank_ic",
    "icir",
    "rank_ic_recent",
    "within_window_retention_ratio",
    "decay_state",
    "direction",
    "strength",
    "turnover",
    "long_short_return",
    "null_ic_n4",
    "null_ic_n1",
    "exceeds_null_n4",
    "exceeds_null_n1",
    "ic_pvalue_approx",
    "passes_direction_threshold",
    "passes_within_window_stability",
    "dev_rank_ic",
    "dev_icir",
    "dev_direction",
    "ic_diff_vs_dev",
    "oos_retention_vs_dev",
    "oos_degradation_vs_dev",
    "sign_preserved_vs_dev",
    "direction_preserved_vs_dev",
    "oos_vs_dev_decay_state",
    "baseline_rank_ic",
    "abs_ic_vs_baseline",
    "beats_baseline_abs_ic",
    "sentinel_zero_flags",
)


def write_results_csv(rows: list[dict[str, Any]], path: Path) -> None:
    """Write flat results table with LF line endings retaining all combinations."""
    with path.open("w", encoding="utf-8", newline="\n") as f:
        writer = csv.writer(f, lineterminator="\n")
        writer.writerow(RESULTS_CSV_COLUMNS)
        for row in rows:
            formatted: list[Any] = []
            for col in RESULTS_CSV_COLUMNS:
                val = row.get(col)
                if col == "sentinel_zero_flags" and isinstance(val, list):
                    formatted.append("|".join(str(x) for x in val))
                elif isinstance(val, float):
                    formatted.append(f"{val:.8f}")
                elif val is None:
                    formatted.append("")
                else:
                    formatted.append(val)
            writer.writerow(formatted)


def write_study_artifacts(
    output_dir: Path,
    *,
    csv_filename: str = "btcusdt_1h_20250701_20261001.csv",
    config: StudyConfig = STUDY_209_CONFIG,
) -> dict[str, Any]:
    """Load the frozen CSV, run the validation study, and write deterministic artifacts."""
    csv_path = output_dir / csv_filename
    df = load_ohlcv_csv(csv_path)
    manifest = build_dataset_manifest(csv_path, df, config=config)
    results = run_validation_study(df, config=config)

    config_path = output_dir / "study_config.json"
    manifest_path = output_dir / "dataset_manifest.json"
    results_json_path = output_dir / "results.json"
    results_csv_path = output_dir / "results_table.csv"

    config_path.write_text(
        json.dumps(config.to_dict(), indent=2, sort_keys=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    results_json_path.write_text(
        json.dumps(results, indent=2, sort_keys=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    all_table_rows = [*results["primary_results"], *results["descriptive_full_results"]]
    write_results_csv(all_table_rows, results_csv_path)

    return {
        "manifest": manifest,
        "results": results,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run Issue #209 single-asset BTC/USDT 1h factor validation study."
    )
    default_dir = (
        Path(__file__).resolve().parents[2] / "studies" / "issue_209_btc_1h"
    )
    parser.add_argument(
        "--study-dir",
        type=Path,
        default=default_dir,
        help="Directory containing frozen OHLCV CSV and study outputs.",
    )
    parser.add_argument(
        "--csv-name",
        type=str,
        default="btcusdt_1h_20250701_20261001.csv",
        help="Filename of the frozen OHLCV CSV inside --study-dir.",
    )
    args = parser.parse_args(argv)
    bundle = write_study_artifacts(args.study_dir, csv_filename=args.csv_name)
    summaries = bundle["results"]["factor_horizon_summaries"]
    print(
        f"Completed study {STUDY_209_CONFIG.study_id}: "
        f"{len(bundle['results']['primary_results'])} primary cells, "
        f"{len(summaries)} factor-horizon summaries written to {args.study_dir}"
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
