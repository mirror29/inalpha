# Expected Synthetic Validation Results

| **Validation Query Target** | **Triggering Synthetic Row(s)** | **Expected Status / Result** |
|---|---|---|
| **Duplicate Candidate Keys** | `C_DUP` | FAILURE (1 row returned) |
| **Duplicate Run Keys** | `R_DUP_RUN` | FAILURE (1 row returned) |
| **Duplicate Trade (`run_key`, `seq`)** | `R_DUP_TRADE` (seq 1) | FAILURE (1 row returned) |
| **Candidate missing Run** | `C_DUP`, `C_ORPHAN`, `C_BAD_FIT` | FAILURE (4 rows returned; `C_DUP` appears twice because the synthetic dataset contains two identical candidate rows) |
| **Run missing Candidate** | `R_ORPHAN` | FAILURE (1 row returned) |
| **Trade missing Run** | `R_MISSING_RUN` | FAILURE (1 row returned) |
| **Trade Count Mismatch** | `R_BAD_MATH` (Reported: 5, Actual: 1); `R_DUP_RUN` (Reported: 1, Actual: 2) | FAILURE (2 rows returned) |
| **Total Fees Mismatch** | `R_BAD_MATH` (Reported: 10, Actual: 2); `R_DUP_RUN` (Reported: 1, Actual: 2) | FAILURE (2 rows returned) |
| **Initial vs `metric_initial_cash` Difference** | `R_BAD_MATH` (10000 vs 5000) | FAILURE (1 row returned) |
| **`total_return_pct` Calculation Error** | `R_BAD_MATH` (Reported: 99, Calculated: 300) | FAILURE (1 row returned) |
| **`to_ts <= from_ts` (Dates)** | `R_BAD_MATH` | FAILURE (1 row returned) |
| **`fee_rate < 0`** | `R_BAD_MATH` (-0.05) | FAILURE (1 row returned) |
| **`quantity <= 0`** | `R_BAD_MATH` trade seq 1 (-5.0) | FAILURE (1 row returned) |
| **`fill_price <= 0`** | `R_BAD_MATH` trade seq 1 (-10.0) | FAILURE (1 row returned) |
| **Missing REQUIRED Fields (8 checks)** | `R_NULLS` (fails `initial_cash`, `total_return_pct`, `metric_initial_cash`, `reported_num_trades`, `total_fees`, `final_equity`, `timeframe`, `num_bars_processed`) | FAILURE (8 rows across 8 distinct queries) |
| **Non-positive `metric_initial_cash`** | `R_ZERO_CASH` (0.0) | FAILURE (1 row returned) |
| **Zero Trades (`COUNT = 0`)** | `R_ZERO_FILL`, `R_NULLS` | INFORMATIONAL (identifies runs with zero actual trade rows) |
| **Negative Fitness** | `C_BAD_FIT` (-5.0) | FAILURE (1 row returned) |
| **Global Returns (`MAX`, `AVG`, `MIN`)** | All `backtest_runs` rows | INFORMATIONAL (summary across all `backtest_runs` rows) |
| **Invalid `num_bars_processed`** | `R_BAD_MATH` (-10), `R_NULLS` (NULL) | FAILURE (2 rows returned) |
| **Unsupported Timeframe** | `R_BAD_MATH` (`7d`), `R_BAD_TF` (`2d`), `R_NULLS` (`NULL`) | FAILURE (3 rows returned) |
| **Annualized Return Mismatch** | `R_ANN_ERR` (Reported: 99.9, Calculated: 10.0) | FAILURE (1 row returned) |
