"""Unit and reproducibility tests for the Issue #209 factor validation study."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from inalpha_factor.validation_study import (
    FACTOR_SPEC_BY_ID,
    STUDY_209_CONFIG,
    STUDY_209_FACTORS,
    STUDY_209_WINDOWS,
    audit_ohlcv_series,
    compute_file_sha256,
    compute_regime_labels,
    compute_study_factors,
    compute_window_forward_return,
    evaluate_window_factor,
    inspect_pair_diagnostics,
    load_ohlcv_csv,
    reindex_to_hourly_grid,
    run_validation_study,
)


def _synthetic_hourly_ohlcv(
    n: int = 300,
    start: str = "2025-07-01T00:00:00Z",
    seed: int = 42,
) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.date_range(start=start, periods=n, freq="1h", tz="UTC", name="timestamp")
    rets = rng.normal(0.0002, 0.008, size=n)
    close = 60000.0 * np.exp(np.cumsum(rets))
    open_ = np.roll(close, 1)
    open_[0] = close[0] * 0.999
    spread = np.abs(rng.normal(0.003, 0.001, size=n)) * close
    high = np.maximum(open_, close) + spread
    low = np.minimum(open_, close) - spread
    volume = rng.uniform(100.0, 2000.0, size=n)
    return pd.DataFrame(
        {
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
        },
        index=idx,
    )


def test_audit_ohlcv_series_accepts_clean_utc_hourly_bars() -> None:
    df = _synthetic_hourly_ohlcv(120)
    report = audit_ohlcv_series(
        df,
        expected_start="2025-07-01T00:00:00Z",
        expected_end="2025-07-06T00:00:00Z",
    )
    assert report.status == "ok"
    assert report.row_count == 120
    assert report.expected_bar_count == 120
    assert report.missing_bar_count == 0
    assert report.duplicate_count == 0
    assert report.is_utc is True
    assert report.is_monotonic_increasing is True
    assert report.is_hour_aligned is True
    assert report.errors == []


def test_audit_ohlcv_series_rejects_naive_unsorted_duplicate_and_misaligned_timestamps() -> None:
    df = _synthetic_hourly_ohlcv(48)

    # 1. Timezone-naive timestamps must fail
    naive_df = df.copy()
    naive_df.index = naive_df.index.tz_localize(None)
    rep_naive = audit_ohlcv_series(naive_df)
    assert rep_naive.status == "invalid"
    assert rep_naive.is_utc is False

    # 2. Out-of-order timestamps must fail
    unsorted_df = df.iloc[[0, 2, 1, *range(3, 48)]]
    rep_unsorted = audit_ohlcv_series(unsorted_df)
    assert rep_unsorted.status == "invalid"
    assert rep_unsorted.is_monotonic_increasing is False

    # 3. Duplicate timestamps must fail
    dup_df = pd.concat([df.iloc[:10], df.iloc[9:]])
    rep_dup = audit_ohlcv_series(dup_df)
    assert rep_dup.status == "invalid"
    assert rep_dup.duplicate_count == 1

    # 4. Non-hour-aligned timestamps must fail
    misaligned_df = df.copy()
    bad_idx = list(misaligned_df.index)
    bad_idx[5] = bad_idx[5] + pd.Timedelta(minutes=15)
    misaligned_df.index = pd.DatetimeIndex(bad_idx)
    rep_misaligned = audit_ohlcv_series(misaligned_df)
    assert rep_misaligned.status == "invalid"
    assert rep_misaligned.is_hour_aligned is False


def test_audit_ohlcv_series_detects_missing_bars_and_corrupt_ohlcv() -> None:
    df = _synthetic_hourly_ohlcv(50)
    # Drop 2 interior bars to create a 2-hour gap
    gapped_df = df.drop(index=[df.index[10], df.index[11]])
    rep_gap = audit_ohlcv_series(
        gapped_df,
        expected_start="2025-07-01T00:00:00Z",
        expected_end="2025-07-03T02:00:00Z",
    )
    assert rep_gap.status == "ok"
    assert rep_gap.missing_bar_count == 2
    assert len(rep_gap.missing_timestamps) == 2

    # Corrupt high < low envelope and negative volume
    corrupt_df = df.copy()
    corrupt_df.iloc[3, corrupt_df.columns.get_loc("high")] = (
        corrupt_df.iloc[3]["low"] - 10.0
    )
    corrupt_df.iloc[4, corrupt_df.columns.get_loc("volume")] = -1.0
    rep_corrupt = audit_ohlcv_series(corrupt_df)
    assert rep_corrupt.status == "invalid"
    assert rep_corrupt.invalid_ohlc_row_count == 1
    assert rep_corrupt.negative_volume_row_count == 1


def test_future_bar_perturbation_never_alters_past_or_current_factor_values() -> None:
    """Modifying bars strictly after timestamp t must not alter f[<= t] (no look-ahead)."""
    base_df = _synthetic_hourly_ohlcv(260, seed=101)
    cutoff_idx = 150

    perturbed_df = base_df.copy()
    # Drastically alter all future bars strictly after cutoff_idx
    perturbed_df.iloc[cutoff_idx + 1 :, perturbed_df.columns.get_loc("close")] *= 3.5
    perturbed_df.iloc[cutoff_idx + 1 :, perturbed_df.columns.get_loc("open")] *= 3.5
    perturbed_df.iloc[cutoff_idx + 1 :, perturbed_df.columns.get_loc("high")] *= 4.0
    perturbed_df.iloc[cutoff_idx + 1 :, perturbed_df.columns.get_loc("low")] *= 0.5
    perturbed_df.iloc[cutoff_idx + 1 :, perturbed_df.columns.get_loc("volume")] *= 25.0

    base_factors = compute_study_factors(base_df)
    perturbed_factors = compute_study_factors(perturbed_df)

    for spec in STUDY_209_FACTORS:
        fid = spec.factor_id
        np.testing.assert_allclose(
            base_factors[fid].iloc[: cutoff_idx + 1].to_numpy(),
            perturbed_factors[fid].iloc[: cutoff_idx + 1].to_numpy(),
            rtol=1e-12,
            atol=1e-12,
            equal_nan=True,
            err_msg=f"Look-ahead leakage detected in {fid} at or before index {cutoff_idx}",
        )
        # Confirm the future perturbation actually changed future values after cutoff_idx
        assert not np.allclose(
            base_factors[fid].iloc[cutoff_idx + 2 :].dropna().to_numpy(),
            perturbed_factors[fid].iloc[cutoff_idx + 2 :].dropna().to_numpy(),
        )


def test_warmup_bars_are_nan_until_sufficient_history_exists() -> None:
    df = _synthetic_hourly_ohlcv(120, seed=7)
    factors = compute_study_factors(df, enforce_warmup=True)

    for spec in STUDY_209_FACTORS:
        series = factors[spec.factor_id]
        assert series.iloc[: spec.warmup_bars].isna().all(), (
            f"{spec.factor_id} must be NaN during first {spec.warmup_bars} warmup bars"
        )
        assert series.iloc[spec.warmup_bars :].notna().all(), (
            f"{spec.factor_id} must be finite after warmup on gap-free data"
        )


def test_gap_handling_does_not_forward_fill_future_targets_or_bridge_factors() -> None:
    """Missing hourly bars must produce NaN targets and isolate post-gap warmup."""
    df = _synthetic_hourly_ohlcv(120, start="2025-07-01T00:00:00Z", seed=19)
    gap_ts = df.index[50]
    gapped_raw = df.drop(index=[gap_ts])

    grid_df = reindex_to_hourly_grid(
        gapped_raw,
        start="2025-07-01T00:00:00Z",
        end="2025-07-06T00:00:00Z",
    )
    assert len(grid_df) == 120
    assert grid_df.loc[gap_ts].isna().all()

    # For horizon h=6, both bar 50 (missing current close) and bar 44 (whose t+6 target is bar 50)
    # must have NaN forward returns, whereas bar 43 (target bar 49) and bar 45 (target bar 51) are valid.
    fwd_6 = compute_window_forward_return(
        grid_df["close"],
        window_start="2025-07-01T00:00:00Z",
        window_end="2025-07-06T00:00:00Z",
        horizon=6,
    )
    assert np.isnan(fwd_6.iloc[50])
    assert np.isnan(fwd_6.iloc[44])
    assert np.isfinite(fwd_6.iloc[43])
    assert np.isfinite(fwd_6.iloc[45])

    # Factor values at the gap and during post-gap warmup must be NaN (never padded across gap)
    factors = compute_study_factors(grid_df, enforce_warmup=True)
    roc = factors["pandas_ta.roc_10"]
    assert np.isnan(roc.iloc[50])
    assert roc.iloc[51 : 51 + 10].isna().all()
    assert np.isfinite(roc.iloc[51 + 10])


def test_window_boundary_excludes_cross_boundary_targets_and_preserves_causal_warmup() -> None:
    """Targets must not cross window_end, while OOS windows use prior history for warmup."""
    # Build a 2-window synthetic series spanning dev end / OOS Q1 start (2026-01-01T00:00:00Z)
    df = _synthetic_hourly_ohlcv(
        400,
        start="2025-12-24T00:00:00Z",
        seed=55,
    )
    dev_win = STUDY_209_WINDOWS[0]  # ends 2026-01-01T00:00:00Z (192 bars from Dec 24)
    oos_win = STUDY_209_WINDOWS[1]  # starts 2026-01-01T00:00:00Z (remaining 208 bars)

    factors_a = compute_study_factors(df)
    cell_dev_a = evaluate_window_factor(
        factors_a["pandas_ta.macd_hist"],
        df["close"],
        window=dev_win,
        factor_spec=FACTOR_SPEC_BY_ID["pandas_ta.macd_hist"],
        horizon=24,
    )
    cell_oos_a = evaluate_window_factor(
        factors_a["pandas_ta.macd_hist"],
        df["close"],
        window=oos_win,
        factor_spec=FACTOR_SPEC_BY_ID["pandas_ta.macd_hist"],
        horizon=24,
    )

    # OOS window has 0 warmup NaNs because causal warmup completed in Development window
    assert cell_oos_a["warmup_or_gap_nan_count"] == 0
    assert cell_oos_a["horizon_tail_nan_count"] == 24
    assert cell_oos_a["sample_size"] == 208 - 24

    # Perturb all OOS bars (>= 2026-01-01T00:00:00Z) and verify Dev window metrics are unchanged
    df_perturbed_oos = df.copy()
    oos_mask = df_perturbed_oos.index >= pd.Timestamp("2026-01-01T00:00:00Z")
    df_perturbed_oos.loc[oos_mask, ["open", "high", "low", "close"]] *= 50.0
    factors_b = compute_study_factors(df_perturbed_oos)
    cell_dev_b = evaluate_window_factor(
        factors_b["pandas_ta.macd_hist"],
        df_perturbed_oos["close"],
        window=dev_win,
        factor_spec=FACTOR_SPEC_BY_ID["pandas_ta.macd_hist"],
        horizon=24,
    )

    assert cell_dev_a["sample_size"] == cell_dev_b["sample_size"]
    assert cell_dev_a["rank_ic"] == pytest.approx(cell_dev_b["rank_ic"], abs=1e-12)
    assert cell_dev_a["icir"] == pytest.approx(cell_dev_b["icir"], abs=1e-12)
    assert cell_dev_a["rank_ic_recent"] == pytest.approx(
        cell_dev_b["rank_ic_recent"], abs=1e-12
    )


def test_diagnostics_expose_sentinel_zeros_for_constant_and_insufficient_series() -> None:
    idx = pd.date_range("2025-07-01T00:00:00Z", periods=60, freq="1h", tz="UTC")
    const_factor = pd.Series(50.0, index=idx)
    varying_fwd = pd.Series(np.linspace(-0.02, 0.03, len(idx)), index=idx)

    diag_const_f = inspect_pair_diagnostics(const_factor, varying_fwd, horizon=1)
    assert diag_const_f.status == "constant_factor"
    assert "constant_factor_rank_ic_sentinel_zero" in diag_const_f.sentinel_zero_flags

    varying_factor = pd.Series(np.linspace(10.0, 90.0, len(idx)), index=idx)
    const_fwd = pd.Series(0.0, index=idx)
    diag_const_r = inspect_pair_diagnostics(varying_factor, const_fwd, horizon=1)
    assert diag_const_r.status == "constant_target"
    assert "constant_target_rank_ic_sentinel_zero" in diag_const_r.sentinel_zero_flags

    tiny_factor = varying_factor.iloc[:2]
    tiny_fwd = varying_fwd.iloc[:2]
    diag_tiny = inspect_pair_diagnostics(tiny_factor, tiny_fwd, horizon=1)
    assert diag_tiny.status == "insufficient_samples"
    assert "sample_size_lt_3_rank_ic_sentinel_zero" in diag_tiny.sentinel_zero_flags

    short_factor = varying_factor.iloc[:10]
    short_fwd = varying_fwd.iloc[:10]
    diag_short = inspect_pair_diagnostics(short_factor, short_fwd, horizon=1)
    assert diag_short.status == "insufficient_icir_segments"
    assert "sample_size_lt_15_icir_sentinel_zero" in diag_short.sentinel_zero_flags


def test_regime_labels_are_strictly_causal_and_freeze_dev_volatility_median() -> None:
    df = _synthetic_hourly_ohlcv(500, start="2025-12-20T00:00:00Z", seed=88)
    reg_a, med_a = compute_regime_labels(
        df["close"],
        dev_start="2025-12-20T00:00:00Z",
        dev_end="2026-01-01T00:00:00Z",
    )

    df_mod = df.copy()
    oos_mask = df_mod.index >= pd.Timestamp("2026-01-01T00:00:00Z")
    df_mod.loc[oos_mask, "close"] *= np.linspace(1.0, 5.0, int(oos_mask.sum()))
    reg_b, med_b = compute_regime_labels(
        df_mod["close"],
        dev_start="2025-12-20T00:00:00Z",
        dev_end="2026-01-01T00:00:00Z",
    )

    assert med_a == pytest.approx(med_b, abs=1e-15)
    dev_slice = df.index < pd.Timestamp("2026-01-01T00:00:00Z")
    pd.testing.assert_series_equal(
        reg_a.loc[dev_slice, "vol_regime"],
        reg_b.loc[dev_slice, "vol_regime"],
    )
    pd.testing.assert_series_equal(
        reg_a.loc[dev_slice, "trend_regime"],
        reg_b.loc[dev_slice, "trend_regime"],
    )


def test_frozen_study_209_dataset_and_artifacts_are_reproducible() -> None:
    study_dir = (
        Path(__file__).resolve().parents[1] / "studies" / "issue_209_btc_1h"
    )
    csv_path = study_dir / "btcusdt_1h_20250701_20261001.csv"
    manifest_path = study_dir / "dataset_manifest.json"
    config_path = study_dir / "study_config.json"
    results_path = study_dir / "results.json"
    table_path = study_dir / "results_table.csv"

    assert csv_path.exists()
    assert manifest_path.exists()
    assert config_path.exists()
    assert results_path.exists()
    assert table_path.exists()

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["dataset_sha256"] == compute_file_sha256(csv_path)
    assert manifest["coverage"]["row_count"] == 10968
    assert manifest["coverage"]["missing_bar_count"] == 0
    assert manifest["coverage"]["duplicate_count"] == 0
    assert manifest["hygiene_checks"]["status"] == "ok"

    df = load_ohlcv_csv(csv_path)
    recomputed = run_validation_study(df, config=STUDY_209_CONFIG)
    committed = json.loads(results_path.read_text(encoding="utf-8"))

    # All 48 primary cells (4 windows x 4 factors x 3 horizons) + 12 full-period cells retained
    assert len(committed["primary_results"]) == 48
    assert len(committed["descriptive_full_results"]) == 12
    assert len(committed["factor_horizon_summaries"]) == 12

    for a, b in zip(
        recomputed["primary_results"], committed["primary_results"], strict=True
    ):
        assert a["window_id"] == b["window_id"]
        assert a["factor_id"] == b["factor_id"]
        assert a["horizon"] == b["horizon"]
        assert a["sample_size"] == b["sample_size"]
        assert a["rank_ic"] == pytest.approx(b["rank_ic"], abs=1e-12)
        assert a["icir"] == pytest.approx(b["icir"], abs=1e-12)
        assert a["rank_ic_recent"] == pytest.approx(b["rank_ic_recent"], abs=1e-12)
        assert a["decay_state"] == b["decay_state"]

