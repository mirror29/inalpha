# Inalpha — Strategy & Backtest Validation Findings

## 1. Purpose

This document summarizes the findings from the strategy-to-backtest validation work.

The validation focuses on:

- data integrity
- candidate → backtest run → fill relationships
- reconciliation of reported and calculated metrics
- required-field and denominator validation
- annualized-return consistency
- basic data-quality anomalies
- reproducibility limitations of the available dataset

The public reproduction uses a fully synthetic dataset. Results from the private sanitized snapshot are described separately and should not be interpreted as independently verified engine behavior.

---

## 2. Validation Environments

Validation was performed using:

- **Excel / Power Query** — exploratory and record-level inspection
- **SQLite** — reproducible SQL-based validation
- **Power BI** — interactive inspection of candidate → run → fill lineage

The SQLite validation script is the primary executable validation artifact.

---

## 3. Synthetic Validation Findings

The synthetic fixture was intentionally constructed to exercise both successful reconciliation and known failure conditions.

### 3.1 Validation Summary

| **Validation Area** | **Synthetic Result** | **Status** |
|---|---|---|
| Candidate key uniqueness | `C_DUP` detected twice | FAILURE DETECTED |
| Run key uniqueness | `R_DUP_RUN` detected twice | FAILURE DETECTED |
| Fill key uniqueness | `R_DUP_TRADE`, seq 1 duplicated | FAILURE DETECTED |
| Candidate → Run relationship | Candidates without runs detected | FAILURE DETECTED |
| Run → Candidate relationship | `R_ORPHAN` references missing candidate | FAILURE DETECTED |
| Fill → Run relationship | `R_MISSING_RUN` references missing run | FAILURE DETECTED |
| Reported trade count vs actual fills | Mismatches detected for `R_BAD_MATH` and `R_DUP_RUN` | FAILURE DETECTED |
| Reported fees vs fill-level fees | Mismatches detected for `R_BAD_MATH` and `R_DUP_RUN` | FAILURE DETECTED |
| `initial_cash` vs `metric_initial_cash` | Difference detected for `R_BAD_MATH` | FAILURE DETECTED |
| `total_return_pct` reconciliation | Difference detected for `R_BAD_MATH` | FAILURE DETECTED |
| Backtest date ordering | Invalid interval detected for `R_BAD_MATH` | FAILURE DETECTED |
| Fee-rate validation | Negative fee rate detected for `R_BAD_MATH` | FAILURE DETECTED |
| Quantity validation | Non-positive quantity detected for `R_BAD_MATH` | FAILURE DETECTED |
| Fill-price validation | Non-positive fill price detected for `R_BAD_MATH` | FAILURE DETECTED |
| Required-field validation | Missing required values detected in `R_NULLS` | FAILURE DETECTED |
| Initial-cash denominator validation | Zero `metric_initial_cash` detected in `R_ZERO_CASH` | FAILURE DETECTED |
| Zero-fill runs | `R_ZERO_FILL` and `R_NULLS` identified | INFORMATIONAL |
| Fitness validation | Negative fitness detected for `C_BAD_FIT` | FAILURE DETECTED |
| Annualized-return validation | Mismatch detected for `R_ANN_ERR` | FAILURE DETECTED |
| Global return summary | MAX = 99.0%, AVG = 11.9%, MIN = 0.0% | INFORMATIONAL |

---

## 4. Clean Control Case

`R_VALID` was included as a positive control case.

Its stored values are internally consistent:

- `metric_initial_cash` = 10,000.50
- `final_equity` = 11,000.55
- `total_return_pct` = 10.0%
- timeframe = `1d`
- bars processed = 365
- annualized return = 10.0%

The return identity therefore reconciles for this synthetic case, and the annualization calculation also reconciles using the documented linear annualization method.

This demonstrates that the validation queries can distinguish an internally consistent record from deliberately malformed records in the synthetic fixture.

---

## 5. Annualized Return Validation

Annualized return was validated using the documented linear annualization convention rather than CAGR.

The calculation uses:

- `total_return_pct`
- `num_bars_processed`
- the supported timeframe's annualization factor

The synthetic fixture includes an intentionally incorrect annualized return:

- `R_ANN_ERR`
- reported annualized return: **99.9%**
- calculated annualized return: **10.0%**

The SQL validation therefore detects the discrepancy.

The validation also explicitly identifies:

- missing or non-positive `num_bars_processed`
- unsupported timeframes
- missing timeframe values

This prevents annualized-return checks from silently treating invalid denominators or unsupported mappings as successful reconciliations.

---

## 6. Required Fields and Invalid Denominators

The validation explicitly checks required run-level fields rather than allowing SQL `NULL` behavior to silently exclude invalid records.

The synthetic `R_NULLS` case contains missing values for:

- `initial_cash`
- `total_return_pct`
- `metric_initial_cash`
- `reported_num_trades`
- `total_fees`
- `final_equity`
- `timeframe`
- `num_bars_processed`

A separate check identifies non-positive `metric_initial_cash`, with `R_ZERO_CASH` providing the zero-denominator case.

Nullable candidate fields such as `fitness` and `last_backtest_run_key` are not automatically classified as failures because their NULL values can be legitimate according to the data model.

---

## 7. Data-Quality Anomalies

The synthetic fixture also tests several basic data-quality conditions.

Detected cases include:

- duplicate candidate keys
- duplicate run keys
- duplicate `(run_key, seq)` fill keys
- missing candidate relationships
- missing run relationships
- invalid date ordering
- negative fee rate
- non-positive trade quantity
- non-positive fill price
- negative fitness

These checks are intended to identify records requiring investigation. They do not by themselves establish that a strategy is profitable or that the underlying backtest engine is incorrect.

---

## 8. Private Snapshot Findings

The original validation work was also performed against the sanitized private snapshot supplied for this issue.

That snapshot contained:

- 57 strategy candidates
- 56 completed backtest runs
- 1,425 simulated fills
- 44 runs containing fills
- 12 runs without fills
- 53 candidates pointing to a latest backtest run

The private snapshot results should be treated as **snapshot-level validation results**, not as independent proof of the engine's calculations.

The private dataset is not included in this repository. No private rows, account information, internal identifiers, credentials, production connections, CSV exports, or PBIX files are required for reproducing the public validation logic.

---

## 9. Reproducibility and Metric Limitations

The available run-level and fill-level data supports independent validation of several metrics and relationships, including:

- key uniqueness
- foreign-key relationships
- fill counts
- fee totals
- cash consistency
- total-return reconciliation
- annualized-return calculation
- basic field and value validation

However, some portfolio-path metrics cannot be independently reconstructed from the available fills alone.

### Metrics requiring an equity curve

The following remain limited by the absence of a persisted per-bar equity curve:

- Sharpe ratio
- Sortino ratio
- annualized volatility
- maximum drawdown
- drawdown duration

Calmar can be calculated from the stored annualized return and maximum drawdown, but the underlying drawdown path cannot be independently reproduced without the equity curve.

A per-bar equity dataset containing at least:

`run_key, bar_ts_utc, equity`

would substantially improve independent validation of these path-dependent metrics.

---

## 10. Interpretation

The validation establishes a reproducible framework for checking the **internal consistency and traceability of backtest outputs** across:

`candidate → backtest run → simulated fill`

It does **not** establish that a strategy is profitable, that reported performance represents live trading performance, or that every engine-level portfolio calculation is independently reproducible from the supplied snapshot.

The Power BI prototype provides an interactive inspection layer over the same lineage, while SQLite provides the executable validation baseline.

The synthetic fixture demonstrates that the checks can detect intentionally introduced integrity, reconciliation, NULL, denominator, and annualization failures.
