# Issue #209 — Single-Asset Crypto Time-Series Factor Stability Study (`BTC/USDT` `1h`)

- **Issue**: [mirror29/inalpha#209](https://github.com/mirror29/inalpha/issues/209)
- **Pre-registration confirmation**: [Issue #209 comment `#issuecomment-6513022958`](https://github.com/mirror29/inalpha/issues/209#issuecomment-6513022958)
- **Study ID**: `issue_209_btc_1h_single_asset_validation`
- **Evaluator module**: `services/factor/src/inalpha_factor/validation_study.py`
- **Alignment & reproducibility tests**: `services/factor/tests/test_validation_study.py`

---

## 1. Study Design & Frozen Dataset Convention

### 1.1 Instrument, Venue & Frozen Interval
| Parameter | Value |
|---|---|
| Instrument / Venue | `BTC/USDT` spot (`BTCUSDT` on `binance_spot`) |
| Source endpoint | `https://data-api.binance.vision/api/v3/klines` |
| Timeframe & convention | `1h` OHLCV, UTC bar-open timestamps, completed bars only |
| Frozen interval | `[2025-07-01T00:00:00Z, 2026-10-01T00:00:00Z)` |
| Dataset file | `btcusdt_1h_20250701_20261001.csv` |
| SHA-256 (`LF`) | `2dde8428dadc0019ce9a9c6df0049d4babb329e10050af8282a4730706dcd0b4` |
| Coverage & gaps | `10,968 / 10,968` hourly bars (`0` missing bars, `0` duplicates, `0` corrupt rows) |

### 1.2 Predeclared Windows
| Window ID | Role | Start (inclusive) | End (exclusive) | Hourly Bars | BTC Return | High-Vol Share | Uptrend Share |
|---|---|---|---|---:|---:|---:|---:|
| `dev` | Development (in-sample) | `2025-07-01T00:00:00Z` | `2026-01-01T00:00:00Z` | `4,416` | `-18.37%` | `50.00%` | `51.23%` |
| `oos_2026_q1` | Out-of-sample 1 | `2026-01-01T00:00:00Z` | `2026-04-01T00:00:00Z` | `2,160` | `-22.24%` | `74.44%` | `45.88%` |
| `oos_2026_q2` | Out-of-sample 2 | `2026-04-01T00:00:00Z` | `2026-07-01T00:00:00Z` | `2,184` | `-14.20%` | `55.17%` | `50.05%` |
| `oos_2026_q3` | Out-of-sample 3 | `2026-07-01T00:00:00Z` | `2026-10-01T00:00:00Z` | `2,208` | `+42.76%` | `35.96%` | `56.07%` |
| `full_period` | Descriptive full period | `2025-07-01T00:00:00Z` | `2026-10-01T00:00:00Z` | `10,968` | `-22.12%` | `52.97%` | `51.03%` |

### 1.3 Predeclared Factors & Horizons
- **Horizons**: `h in {1, 6, 24}` hourly bars (`r[t, t+h] = close[t+h] / close[t] - 1`).
- **Core factors** (`PandasTAAdapter` fixed parameters, no tuning):
  1. `pandas_ta.rsi_14` — Wilder RSI(14), catalog `direction_hint = -1`, causal warmup `14` bars.
  2. `pandas_ta.macd_hist` — `(EMA(close,12) - EMA(close,26) - EMA(MACD,9)) / close`, catalog `direction_hint = +1`, causal warmup `33` bars.
  3. `pandas_ta.atr_pct_14` — `RMA(TrueRange, 14) / close`, catalog `direction_hint = None`, causal warmup `14` bars.
- **Simple baseline**:
  - `pandas_ta.roc_10` — `(close[t] / close[t-10] - 1) * 100`, catalog `direction_hint = +1`, causal warmup `10` bars.

---

## 2. Alignment, Causality & Methodological Limitations

### 2.1 Alignment & Leakage Safeguards
1. **Strict UTC hourly grid (`reindex_to_hourly_grid`)**: Input bars are audited for UTC timezone awareness, strictly monotonic increasing timestamps, zero duplicates, exact 1-hour boundaries, and valid positive OHLCV relationships before reindexing onto the complete UTC hourly grid (`fill_value=np.nan`, never forward-filled).
2. **Causal factor computation & gap isolation (`compute_study_factors`)**: Each contiguous gap-free segment of valid OHLCV bars is passed to `PandasTAAdapter().compute(...)` independently and masked for the first `warmup_bars` observations. This guarantees:
   - Perturbing any future bar `t' > t` never alters `f[<= t]`.
   - Warmup values (`ewm` / `_rma` / `pct_change`) are `NaN` until full lookback history exists.
   - Missing hourly bars never allow `Series.pct_change` padding or recursive EMA state to bridge across gaps.
3. **Window-bounded forward targets (`compute_window_forward_return`)**: For each window `[window_start, window_end)`, `close` is sliced to the window before computing `_forward_return(window_close, h)`. The trailing `h` bars in every window receive `NaN` targets so no target return ever crosses `window_end`, while OOS windows (`oos_2026_q1`, `oos_2026_q2`, `oos_2026_q3`) receive causal factor warmup from preceding bars `< window_start`.

### 2.2 Known Statistical Approximations in `inalpha_factor.effectiveness`
- **Overlapping forward returns & serial dependence**: For `h = 6` and `h = 24`, adjacent hourly forward returns share `h - 1` hourly price changes. Sample Spearman correlations (`_rank_ic`) therefore exhibit strong serial correlation and widened finite-sample variance compared with independent observations.
- **Heuristic effective sample size in `null_ic_benchmark` and `ic_pvalue`**: Both functions use `n_eff = max(4, sample_size // horizon)` and `sigma = 1 / sqrt(n_eff - 1)`. This `1/h` reduction is a heuristic floor rather than a formal Newey–West / block-bootstrap HAC estimator, and the Bailey–López de Prado `E[max | null]` approximation assumes independent candidates (whereas `rsi_14`, `macd_hist`, and `roc_10` are correlated price oscillators). We report `null_ic_benchmark` with `N = 4` (the four predeclared series in this study) alongside `N = 1` (`sigma`).
- **5-segment chronological ICIR**: `_icir` partitions aligned valid pairs into `_ICIR_SEGMENTS = 5` equal chronological chunks within a window and computes `mean(seg_ic) / std(seg_ic, ddof=1)`. Within a single quarterly window (~2,160 bars), each segment covers ~18 days; ICIR therefore reflects intra-quarter consistency rather than multi-year cross-sectional stability.
- **Sentinel `0.0` cases**: `effectiveness.py` returns `0.0` when `sample_size < 3`, `sample_size < 15` (ICIR), `< 2` valid segments exist, segment IC standard deviation is zero, or either series is constant. `validation_study.py` inspects every pair via `inspect_pair_diagnostics` and records explicit `status` and `sentinel_zero_flags` so degenerate zeros are never conflated with valid zero correlation. All 48 primary cells and 12 descriptive full-period cells in this study have `status = "ok"` and empty `sentinel_zero_flags`.

### 2.3 Cross-Window Degradation Convention
For each `(factor, horizon)`, the reference IC (`dev_rank_ic`) and reference direction (`dev_direction`) are frozen on `dev` (`2025 H2`) and never re-selected in OOS:
- **Signed OOS retention vs. Dev**: `oos_retention_vs_dev = rank_ic_oos / dev_rank_ic` (`>= 0.60` indicates same-sign retention of at least 60% of development magnitude; `< 0` indicates a sign flip).
- **Signed OOS degradation vs. Dev**: `oos_degradation_vs_dev = 1 - (rank_ic_oos / dev_rank_ic)` (`0.0` = no degradation, `+1.0` = 100% decay to zero, `> 1.0` = sign flip, `< 0.0` = stronger OOS magnitude in the development direction).
- **Cross-window decay state (`oos_vs_dev_decay_state`)**: Evaluates `decay_state(dev_rank_ic, rank_ic_oos)` using the service's 60% same-sign retention rule.

---

## 3. Empirical Results Across Development & OOS Windows

### 3.1 Summary by Factor & Horizon
| Factor | Role | `h` | Dev Rank IC | Dev ICIR | OOS Q1 IC | OOS Q2 IC | OOS Q3 IC | Mean OOS IC | Mean Retention vs Dev | Mean Degradation vs Dev | OOS Sign Match | OOS `stable` vs Dev | OOS `|IC| > null(N=4)` | Verdict |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| `pandas_ta.rsi_14` | core | `1` | `-0.0387` | `-1.232` | `-0.0163` | `-0.0421` | `-0.0415` | `-0.0333` | `+86.0%` | `+14.0%` | `3/3` | `2/3` | `2/3` | `partially_stable_regime_dependent` |
| `pandas_ta.rsi_14` | core | `6` | `-0.0672` | `-0.917` | `-0.0012` | `-0.0162` | `-0.0435` | `-0.0203` | `+30.2%` | `+69.8%` | `3/3` | `1/3` | `0/3` | `fading_in_oos` |
| `pandas_ta.rsi_14` | core | `24` | `-0.0429` | `-0.367` | `+0.0521` | `+0.0259` | `-0.0570` | `+0.0070` | `-16.3%` | `+116.3%` | `1/3` | `1/3` | `0/3` | `unstable_sign_flip_or_decay` |
| `pandas_ta.macd_hist` | core | `1` | `-0.0141` | `-0.704` | `-0.0271` | `-0.0479` | `-0.0128` | `-0.0293` | `+207.9%` | `-107.9%` | `3/3` | `3/3` | `2/3` | `weak_in_sample` |
| `pandas_ta.macd_hist` | core | `6` | `-0.0412` | `-0.866` | `-0.0496` | `+0.0031` | `+0.0209` | `-0.0085` | `+20.7%` | `+79.3%` | `1/3` | `1/3` | `0/3` | `unstable_sign_flip_or_decay` |
| `pandas_ta.macd_hist` | core | `24` | `-0.0205` | `-0.532` | `+0.0731` | `+0.0113` | `+0.1268` | `+0.0704` | `-343.1%` | `+443.1%` | `0/3` | `0/3` | `1/3` | `unstable_sign_flip_or_decay` |
| `pandas_ta.atr_pct_14` | core | `1` | `-0.0039` | `+0.095` | `+0.0099` | `-0.0128` | `+0.0170` | `+0.0047` | `-120.3%` | `+220.3%` | `1/3` | `1/3` | `0/3` | `weak_in_sample` |
| `pandas_ta.atr_pct_14` | core | `6` | `-0.0442` | `-0.356` | `+0.0257` | `-0.0273` | `+0.0546` | `+0.0177` | `-40.0%` | `+140.0%` | `1/3` | `1/3` | `0/3` | `unstable_sign_flip_or_decay` |
| `pandas_ta.atr_pct_14` | core | `24` | `-0.1040` | `-0.502` | `-0.0402` | `-0.0506` | `+0.0948` | `+0.0013` | `-1.3%` | `+101.3%` | `2/3` | `0/3` | `0/3` | `fading_in_oos` |
| `pandas_ta.roc_10` | baseline | `1` | `-0.0353` | `-1.336` | `-0.0279` | `-0.0383` | `-0.0436` | `-0.0366` | `+103.7%` | `-3.7%` | `3/3` | `3/3` | `3/3` | `stable_across_oos` |
| `pandas_ta.roc_10` | baseline | `6` | `-0.0514` | `-0.824` | `-0.0522` | `-0.0112` | `-0.0332` | `-0.0322` | `+62.7%` | `+37.3%` | `3/3` | `2/3` | `0/3` | `partially_stable_regime_dependent` |
| `pandas_ta.roc_10` | baseline | `24` | `-0.0401` | `-0.568` | `+0.0323` | `+0.0095` | `+0.0318` | `+0.0245` | `-61.2%` | `+161.2%` | `0/3` | `0/3` | `0/3` | `unstable_sign_flip_or_decay` |

### 3.2 Detailed Window Results (`h = 1`, `6`, `24`)

#### Horizon `h = 1` (`null_ic_benchmark(N=4)` = `0.0159` on Dev, `0.0224–0.0226` on OOS quarters)
| Window | Factor | Sample Size | Rank IC | ICIR | Recent IC | Within-Window Decay | Dir | OOS vs Dev Decay | Beats `roc_10` `|IC|` |
|---|---|---:|---:|---:|---:|---|---:|---|---|
| `dev` | `pandas_ta.rsi_14` | `4,401` | `-0.0387` | `-1.232` | `-0.0430` | `stable` | `-1` | `stable` (ref) | Yes (`+0.0034`) |
| `oos_2026_q1` | `pandas_ta.rsi_14` | `2,159` | `-0.0163` | `-1.003` | `-0.0110` | `stable` | `0` | `fading` (`42.1%` ret) | No (`-0.0116`) |
| `oos_2026_q2` | `pandas_ta.rsi_14` | `2,183` | `-0.0421` | `-2.015` | `-0.0230` | `fading` | `-1` | `stable` (`108.6%` ret) | Yes (`+0.0038`) |
| `oos_2026_q3` | `pandas_ta.rsi_14` | `2,207` | `-0.0415` | `-1.423` | `-0.0441` | `stable` | `-1` | `stable` (`107.2%` ret) | No (`-0.0021`) |
| `dev` | `pandas_ta.macd_hist` | `4,382` | `-0.0141` | `-0.704` | `-0.0075` | `fading` | `0` | `stable` (ref) | No (`-0.0212`) |
| `oos_2026_q1` | `pandas_ta.macd_hist` | `2,159` | `-0.0271` | `-0.598` | `-0.0116` | `fading` | `-1` | `stable` (`192.7%` ret) | No (`-0.0008`) |
| `oos_2026_q2` | `pandas_ta.macd_hist` | `2,183` | `-0.0479` | `-1.644` | `-0.0676` | `stable` | `-1` | `stable` (`340.1%` ret) | Yes (`+0.0096`) |
| `oos_2026_q3` | `pandas_ta.macd_hist` | `2,207` | `-0.0128` | `-0.596` | `+0.0056` | `decaying` | `0` | `stable` (`90.9%` ret) | No (`-0.0309`) |
| `dev` | `pandas_ta.atr_pct_14` | `4,401` | `-0.0039` | `+0.095` | `+0.0184` | `decaying` | `0` | `stable` (ref) | No (`-0.0314`) |
| `oos_2026_q1` | `pandas_ta.atr_pct_14` | `2,159` | `+0.0099` | `+0.316` | `+0.0422` | `stable` | `0` | `decaying` (`-252.8%` ret) | No (`-0.0180`) |
| `oos_2026_q2` | `pandas_ta.atr_pct_14` | `2,183` | `-0.0128` | `-0.229` | `-0.0058` | `fading` | `0` | `stable` (`326.5%` ret) | No (`-0.0255`) |
| `oos_2026_q3` | `pandas_ta.atr_pct_14` | `2,207` | `+0.0170` | `+0.639` | `+0.0050` | `fading` | `0` | `decaying` (`-434.8%` ret) | No (`-0.0267`) |
| `dev` | `pandas_ta.roc_10` | `4,405` | `-0.0353` | `-1.336` | `-0.0358` | `stable` | `-1` | `stable` (ref) | Baseline |
| `oos_2026_q1` | `pandas_ta.roc_10` | `2,159` | `-0.0279` | `-0.768` | `-0.0222` | `stable` | `-1` | `stable` (`79.0%` ret) | Baseline |
| `oos_2026_q2` | `pandas_ta.roc_10` | `2,183` | `-0.0383` | `-1.652` | `-0.0354` | `stable` | `-1` | `stable` (`108.4%` ret) | Baseline |
| `oos_2026_q3` | `pandas_ta.roc_10` | `2,207` | `-0.0436` | `-3.376` | `-0.0474` | `stable` | `-1` | `stable` (`123.6%` ret) | Baseline |

#### Horizon `h = 6` (`null_ic_benchmark(N=4)` = `0.0389–0.0390` on Dev, `0.0550–0.0556` on OOS quarters)
| Window | Factor | Sample Size | Rank IC | ICIR | Recent IC | Within-Window Decay | Dir | OOS vs Dev Decay | Beats `roc_10` `|IC|` |
|---|---|---:|---:|---:|---:|---|---:|---|---|
| `dev` | `pandas_ta.rsi_14` | `4,396` | `-0.0672` | `-0.917` | `-0.1439` | `stable` | `-1` | `stable` (ref) | Yes (`+0.0159`) |
| `oos_2026_q1` | `pandas_ta.rsi_14` | `2,154` | `-0.0012` | `-0.491` | `-0.0016` | `stable` | `0` | `fading` (`1.8%` ret) | No (`-0.0511`) |
| `oos_2026_q2` | `pandas_ta.rsi_14` | `2,178` | `-0.0162` | `-0.475` | `+0.0073` | `decaying` | `0` | `fading` (`24.1%` ret) | Yes (`+0.0051`) |
| `oos_2026_q3` | `pandas_ta.rsi_14` | `2,202` | `-0.0435` | `-0.630` | `-0.0321` | `stable` | `-1` | `stable` (`64.8%` ret) | Yes (`+0.0104`) |
| `dev` | `pandas_ta.macd_hist` | `4,377` | `-0.0412` | `-0.866` | `-0.0582` | `stable` | `-1` | `stable` (ref) | No (`-0.0102`) |
| `oos_2026_q1` | `pandas_ta.macd_hist` | `2,154` | `-0.0496` | `-0.520` | `-0.0207` | `fading` | `-1` | `stable` (`120.3%` ret) | No (`-0.0027`) |
| `oos_2026_q2` | `pandas_ta.macd_hist` | `2,178` | `+0.0031` | `+0.050` | `-0.0539` | `decaying` | `0` | `decaying` (`-7.5%` ret) | No (`-0.0081`) |
| `oos_2026_q3` | `pandas_ta.macd_hist` | `2,202` | `+0.0209` | `+0.142` | `+0.0805` | `stable` | `+1` | `decaying` (`-50.6%` ret) | No (`-0.0123`) |
| `dev` | `pandas_ta.atr_pct_14` | `4,396` | `-0.0442` | `-0.356` | `+0.0256` | `decaying` | `-1` | `stable` (ref) | No (`-0.0072`) |
| `oos_2026_q1` | `pandas_ta.atr_pct_14` | `2,154` | `+0.0257` | `+0.277` | `+0.1081` | `stable` | `+1` | `decaying` (`-58.2%` ret) | No (`-0.0265`) |
| `oos_2026_q2` | `pandas_ta.atr_pct_14` | `2,178` | `-0.0273` | `-0.270` | `+0.0212` | `decaying` | `-1` | `stable` (`61.8%` ret) | Yes (`+0.0161`) |
| `oos_2026_q3` | `pandas_ta.atr_pct_14` | `2,202` | `+0.0546` | `+0.530` | `-0.0147` | `decaying` | `+1` | `decaying` (`-123.6%` ret) | Yes (`+0.0214`) |
| `dev` | `pandas_ta.roc_10` | `4,400` | `-0.0514` | `-0.824` | `-0.0927` | `stable` | `-1` | `stable` (ref) | Baseline |
| `oos_2026_q1` | `pandas_ta.roc_10` | `2,154` | `-0.0522` | `-0.726` | `-0.0469` | `stable` | `-1` | `stable` (`101.7%` ret) | Baseline |
| `oos_2026_q2` | `pandas_ta.roc_10` | `2,178` | `-0.0112` | `-0.318` | `-0.0389` | `stable` | `0` | `fading` (`21.7%` ret) | Baseline |
| `oos_2026_q3` | `pandas_ta.roc_10` | `2,202` | `-0.0332` | `-1.042` | `+0.0077` | `decaying` | `-1` | `stable` (`64.6%` ret) | Baseline |

#### Horizon `h = 24` (`null_ic_benchmark(N=4)` = `0.0782–0.0784` on Dev, `0.1109–0.1122` on OOS quarters)
| Window | Factor | Sample Size | Rank IC | ICIR | Recent IC | Within-Window Decay | Dir | OOS vs Dev Decay | Beats `roc_10` `|IC|` |
|---|---|---:|---:|---:|---:|---|---:|---|---|
| `dev` | `pandas_ta.rsi_14` | `4,378` | `-0.0429` | `-0.367` | `-0.1487` | `stable` | `-1` | `stable` (ref) | Yes (`+0.0028`) |
| `oos_2026_q1` | `pandas_ta.rsi_14` | `2,136` | `+0.0521` | `-0.044` | `-0.0011` | `decaying` | `+1` | `decaying` (`-121.5%` ret) | Yes (`+0.0199`) |
| `oos_2026_q2` | `pandas_ta.rsi_14` | `2,160` | `+0.0259` | `-0.177` | `+0.2369` | `stable` | `+1` | `decaying` (`-60.3%` ret) | Yes (`+0.0164`) |
| `oos_2026_q3` | `pandas_ta.rsi_14` | `2,184` | `-0.0570` | `-0.441` | `-0.1800` | `stable` | `-1` | `stable` (`132.9%` ret) | Yes (`+0.0252`) |
| `dev` | `pandas_ta.macd_hist` | `4,359` | `-0.0205` | `-0.532` | `-0.0404` | `stable` | `-1` | `stable` (ref) | No (`-0.0196`) |
| `oos_2026_q1` | `pandas_ta.macd_hist` | `2,136` | `+0.0731` | `+0.954` | `+0.0770` | `stable` | `+1` | `decaying` (`-356.4%` ret) | Yes (`+0.0409`) |
| `oos_2026_q2` | `pandas_ta.macd_hist` | `2,160` | `+0.0113` | `-0.007` | `+0.0872` | `stable` | `0` | `decaying` (`-54.9%` ret) | Yes (`+0.0018`) |
| `oos_2026_q3` | `pandas_ta.macd_hist` | `2,184` | `+0.1268` | `+0.822` | `+0.0968` | `stable` | `+1` | `decaying` (`-617.9%` ret) | Yes (`+0.0950`) |
| `dev` | `pandas_ta.atr_pct_14` | `4,378` | `-0.1040` | `-0.502` | `+0.0662` | `decaying` | `-1` | `stable` (ref) | Yes (`+0.0639`) |
| `oos_2026_q1` | `pandas_ta.atr_pct_14` | `2,136` | `-0.0402` | `-0.222` | `+0.0176` | `decaying` | `-1` | `fading` (`38.7%` ret) | Yes (`+0.0079`) |
| `oos_2026_q2` | `pandas_ta.atr_pct_14` | `2,160` | `-0.0506` | `-0.253` | `-0.0249` | `fading` | `-1` | `fading` (`48.6%` ret) | Yes (`+0.0411`) |
| `oos_2026_q3` | `pandas_ta.atr_pct_14` | `2,184` | `+0.0948` | `+0.456` | `+0.0012` | `fading` | `+1` | `decaying` (`-91.1%` ret) | Yes (`+0.0629`) |
| `dev` | `pandas_ta.roc_10` | `4,382` | `-0.0401` | `-0.568` | `-0.0813` | `stable` | `-1` | `stable` (ref) | Baseline |
| `oos_2026_q1` | `pandas_ta.roc_10` | `2,136` | `+0.0323` | `+0.068` | `+0.0166` | `fading` | `+1` | `decaying` (`-80.5%` ret) | Baseline |
| `oos_2026_q2` | `pandas_ta.roc_10` | `2,160` | `+0.0095` | `-0.257` | `+0.1220` | `stable` | `0` | `decaying` (`-23.7%` ret) | Baseline |
| `oos_2026_q3` | `pandas_ta.roc_10` | `2,184` | `+0.0318` | `-0.030` | `-0.0586` | `decaying` | `+1` | `decaying` (`-79.4%` ret) | Baseline |

---

## 4. Key Findings, Regime Sensitivity & Validation Failures

### 4.1 Only 1-Hour Mean Reversion Persists Out-of-Sample, and Simple `roc_10` Matches or Beats Core Factors
- **`pandas_ta.roc_10` (`h = 1`) is the only configuration that passes every OOS stability criterion (`stable_across_oos`)**:
  - Although cataloged with `direction_hint = +1` (momentum), 10-hour rate-of-change acts empirically as a **short-horizon reversal** signal (`direction = -1`) on `BTC/USDT` `1h` bars across Development (`-0.0353`) and all three OOS windows (`-0.0279`, `-0.0383`, `-0.0436`).
  - It retains `79.0%`, `108.4%`, and `123.6%` of its Development IC magnitude across Q1, Q2, and Q3 (`3/3` OOS windows classified as `stable` vs. Dev and `3/3` exceeding `null_ic_benchmark(N=4)`).
- **`pandas_ta.rsi_14` (`h = 1`) is directionally consistent (`3/3` negative sign in OOS) but regime-sensitive (`partially_stable_regime_dependent`)**:
  - In Development (`-0.0387`), OOS Q2 (`-0.0421`), and OOS Q3 (`-0.0415`), `rsi_14` exceeds both `|IC| >= 0.02` and `null_ic_benchmark(N=4)`.
  - However, during the high-volatility drawdown of OOS Q1 (`74.4%` high-vol bars, BTC `-22.2%`), `rsi_14` (`h = 1`) weakens to `-0.0163` (`42.1%` retention vs. Dev, `fading`), losing to simple `roc_10` (`-0.0279`).

### 4.2 Why Factors Fail at `h = 6` and `h = 24`
1. **`pandas_ta.rsi_14` at `h = 6` and `h = 24`**:
   - At `h = 6`, `rsi_14` has strong in-sample mean reversion (`-0.0672`, exceeding `null_ic_n4 = 0.0389`), but collapses to `-0.0012` in OOS Q1 and `-0.0162` in OOS Q2 (`69.8%` mean degradation; `0/3` OOS windows exceed `null_ic_n4 = 0.055`).
   - At `h = 24`, `rsi_14` does not clear `null_ic_n4 = 0.0782` even in Development (`-0.0429`) and flips sign to positive (`+0.0521`, `+0.0259`) in OOS Q1 and Q2.
2. **`pandas_ta.macd_hist` fails across all three horizons**:
   - At `h = 1`, `macd_hist` is too weak in Development (`-0.0141`, failing `|IC| >= 0.02` and `null_ic_n4 = 0.0159`) and contrary to its `+1` momentum hint.
   - At `h = 6` and `h = 24`, `macd_hist` exhibits negative IC during the 2025 H2 development window (`-0.0412` and `-0.0205`) but flips to positive momentum in OOS (`+0.0731` in Q1 and `+0.1268` in Q3 at `h = 24`). While `full_period` `decay_state` labels `macd_hist` (`h = 24`) as `stable` (`full_period` IC `+0.0303`, `recent_ic` `+0.1013`), comparing against the frozen Development reference exposes that the signal completely inverted sign between Development and OOS.
3. **`pandas_ta.atr_pct_14` fails as a directional predictor**:
   - As an unsigned volatility level (`ATR(14)/close`), `atr_pct_14` has near-zero predictive power at `h = 1` (`Dev IC = -0.0039`).
   - At `h = 24`, `atr_pct_14` appears strongly negative in Development (`-0.1040`, exceeding `null_ic_n4 = 0.0782`), because elevated volatility in 2025 H2 coincided with selloffs (`-18.4%`). Even within Development, however, its trailing third flipped positive (`rank_ic_recent = +0.0662`, `within-window decay_state = decaying`), and in OOS Q3 (`+42.8%` rally) its 24-hour Rank IC reversed to `+0.0948`.

### 4.3 Regime Sensitivity (Causal Volatility & Trend Splits)
Using causal 24-bar realized return volatility split at the frozen Development median (`vol_24_dev_median = 0.003503`, ~`0.35%` hourly std) and causal 24-bar return sign (`ret_24 > 0` uptrend vs. `<= 0` downtrend):

| Factor (`h`) | Scope | Low-Vol IC (`n`) | High-Vol IC (`n`) | Uptrend IC (`n`) | Downtrend IC (`n`) | Regime Takeaway |
|---|---|---|---|---|---|---|
| `rsi_14` (`h=1`) | `dev` | `-0.0509` (`2,195`) | `-0.0408` (`2,196`) | `-0.0222` (`2,250`) | `-0.0191` (`2,141`) | Stable negative mean reversion in both vol regimes |
| `rsi_14` (`h=1`) | `oos_pooled` | `-0.0321` (`2,945`) | `-0.0305` (`3,604`) | `-0.0442` (`3,321`) | `-0.0366` (`3,228`) | Clears `null_ic_n4` across all 4 pooled OOS regime buckets |
| `roc_10` (`h=1`) | `dev` | `-0.0528` (`2,195`) | `-0.0345` (`2,196`) | `-0.0227` (`2,250`) | `-0.0229` (`2,141`) | Consistent 1h reversal across volatility and trend splits |
| `roc_10` (`h=1`) | `oos_pooled` | `-0.0322` (`2,945`) | `-0.0398` (`3,604`) | `-0.0448` (`3,321`) | `-0.0389` (`3,228`) | Stronger high-vol OOS resilience (`-0.0398`) than `rsi_14` (`-0.0305`) |
| `macd_hist` (`h=24`) | `dev` | `-0.0680` (`2,184`) | `+0.0324` (`2,175`) | `-0.0268` (`2,237`) | `-0.0030` (`2,122`) | Sign flips between Low-Vol (`-0.0680`) and High-Vol (`+0.0324`) even in Dev |
| `macd_hist` (`h=24`) | `oos_pooled` | `+0.0419` (`2,913`) | `+0.0972` (`3,567`) | `+0.0725` (`3,281`) | `+0.0764` (`3,199`) | Momentum continuation dominates high-vol OOS bars |
| `atr_pct_14` (`h=24`) | `dev` | `-0.0751` (`2,184`) | `-0.0706` (`2,194`) | `-0.1235` (`2,237`) | `-0.0778` (`2,141`) | Negative in Dev bear drift, but reverses in OOS Q3 bull breakout (`+0.0948`) |
| `atr_pct_14` (`h=24`) | `oos_pooled` | `-0.0029` (`2,913`) | `+0.0036` (`3,567`) | `-0.0563` (`3,281`) | `+0.0439` (`3,199`) | Opposing signs in OOS uptrend (`-0.0563`) vs downtrend (`+0.0439`) cancel out |

### 4.4 Evaluation of Operational Service Heuristics
- **`|Rank IC| >= 0.02` direction threshold**: Useful as a coarse filter for `h = 1` (where `null_ic_benchmark(N=4)` is `0.0159–0.0226`), but **too permissive for `h = 6` and `h = 24`**, where overlapping returns inflate the noise floor to `0.055` (`h = 6`) and `0.111` (`h = 24`) on quarterly windows. Several cells at `h = 6` and `h = 24` receive non-zero `direction = ±1` from `|IC| >= 0.02` despite lying well below `null_ic_benchmark`.
- **Within-window `decay_state` (`rank_ic_recent` vs. window `rank_ic`) vs. cross-window OOS validation**: Within-window `decay_state` correctly flagged `atr_pct_14` (`h = 6, 24`) as `decaying` inside Development before OOS began, but labeled `rsi_14` (`h = 6, 24`) and `macd_hist` (`h = 6, 24`) as `stable` in Development even though both degraded or flipped sign in OOS. Evaluating `decay_state(dev_rank_ic, oos_rank_ic)` across disjoint OOS windows is therefore essential before trusting single-window stability labels.

---

## 5. Reproducibility

All artifacts in `services/factor/studies/issue_209_btc_1h/` (`study_config.json`, `dataset_manifest.json`, `results.json`, `results_table.csv`) can be regenerated and verified offline without credentials, network access, or a running database:

```bash
cd services/factor
uv run python -m inalpha_factor.validation_study
uv run pytest tests/test_validation_study.py -q
```

