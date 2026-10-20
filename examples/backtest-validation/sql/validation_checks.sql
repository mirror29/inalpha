-- ============================================================
-- Inalpha Backtest Validation Checks
-- Synthetic/public reproduction queries
--
-- Output convention:
--   VALIDATION FAILURE: ... = failed validation condition
--   INFORMATIONAL: ...    = diagnostic / non-failure output
--
-- NULL values are treated as missing data, not as zero.
-- Dedicated missing-value checks handle NULLs separately.
-- ============================================================


-- ============================================================
-- 1. DUPLICATE CANDIDATE KEYS
-- ============================================================

SELECT
    'VALIDATION FAILURE: duplicate candidate key' AS validation_check,
    candidate_key,
    COUNT(*) AS row_count
FROM candidates
GROUP BY candidate_key
HAVING COUNT(*) > 1;


-- ============================================================
-- 2. DUPLICATE RUN KEYS
-- ============================================================

SELECT
    'VALIDATION FAILURE: duplicate run key' AS validation_check,
    run_key,
    COUNT(*) AS row_count
FROM backtest_runs
GROUP BY run_key
HAVING COUNT(*) > 1;


-- ============================================================
-- 3. DUPLICATE TRADE KEYS
-- ============================================================

SELECT
    'VALIDATION FAILURE: duplicate trade key' AS validation_check,
    run_key,
    seq,
    COUNT(*) AS row_count
FROM backtest_trades
GROUP BY run_key, seq
HAVING COUNT(*) > 1;


-- ============================================================
-- 4. CANDIDATE WITHOUT A BACKTEST RUN
--
-- Informational only.
-- A candidate may legitimately exist before or without a backtest.
-- Broken run-to-candidate references remain a validation failure
-- in check #5.
-- ============================================================

SELECT
    'INFORMATIONAL: candidate has no backtest run' AS validation_check,
    c.candidate_key
FROM candidates c
LEFT JOIN backtest_runs r
    ON c.candidate_key = r.candidate_key
WHERE r.candidate_key IS NULL;


-- ============================================================
-- 5. BACKTEST RUN WITHOUT CANDIDATE
-- ============================================================

SELECT
    'VALIDATION FAILURE: run references missing candidate' AS validation_check,
    r.run_key,
    r.candidate_key
FROM backtest_runs r
LEFT JOIN candidates c
    ON r.candidate_key = c.candidate_key
WHERE c.candidate_key IS NULL;


-- ============================================================
-- 6. TRADE WITHOUT BACKTEST RUN
-- ============================================================

SELECT
    'VALIDATION FAILURE: trade references missing run' AS validation_check,
    t.run_key,
    t.seq
FROM backtest_trades t
LEFT JOIN backtest_runs r
    ON t.run_key = r.run_key
WHERE r.run_key IS NULL;


-- ============================================================
-- 7. REPORTED TRADE COUNT VS ACTUAL FILLS
--
-- NULL reported_num_trades is handled by the dedicated
-- missing-field check below.
-- ============================================================

SELECT
    'VALIDATION FAILURE: reported trade count mismatch' AS validation_check,
    r.run_key,
    r.reported_num_trades,
    COUNT(t.run_key) AS actual_trade_count
FROM backtest_runs r
LEFT JOIN backtest_trades t
    ON r.run_key = t.run_key
GROUP BY r.run_key, r.reported_num_trades
HAVING r.reported_num_trades IS NOT NULL
   AND r.reported_num_trades <> COUNT(t.run_key);


-- ============================================================
-- 8. REPORTED TOTAL FEES VS ACTUAL FEES
--
-- NULL total_fees is handled by the dedicated missing-field
-- check below.
-- ============================================================

SELECT
    'VALIDATION FAILURE: total fees mismatch' AS validation_check,
    r.run_key,
    r.total_fees AS reported_total_fees,
    COALESCE(SUM(t.fee), 0) AS actual_total_fees
FROM backtest_runs r
LEFT JOIN backtest_trades t
    ON r.run_key = t.run_key
GROUP BY r.run_key, r.total_fees
HAVING r.total_fees IS NOT NULL
   AND ABS(
       r.total_fees - COALESCE(SUM(t.fee), 0)
   ) > 0.000001;


-- ============================================================
-- 9. INITIAL CASH VS METRIC INITIAL CASH
--
-- NULL values are handled separately.
-- ============================================================

SELECT
    'VALIDATION FAILURE: initial cash mismatch' AS validation_check,
    run_key,
    initial_cash,
    metric_initial_cash
FROM backtest_runs
WHERE initial_cash IS NOT NULL
  AND metric_initial_cash IS NOT NULL
  AND ABS(initial_cash - metric_initial_cash) > 0.000001;


-- ============================================================
-- 10. TOTAL RETURN CALCULATION
--
-- Authoritative denominator:
-- metric_initial_cash
--
-- Formula:
-- (final_equity - metric_initial_cash)
-- / metric_initial_cash * 100
-- ============================================================

SELECT
    'VALIDATION FAILURE: total return mismatch' AS validation_check,
    run_key,
    total_return_pct,
    ROUND(
        (final_equity - metric_initial_cash)
        / metric_initial_cash * 100,
        4
    ) AS calculated_total_return_pct
FROM backtest_runs
WHERE metric_initial_cash IS NOT NULL
  AND metric_initial_cash > 0
  AND final_equity IS NOT NULL
  AND total_return_pct IS NOT NULL
  AND ABS(
      total_return_pct -
      (
          (final_equity - metric_initial_cash)
          / metric_initial_cash * 100
      )
  ) > 0.000001;


-- ============================================================
-- 11. INVALID DATE RANGE
--
-- Missing dates are not classified as an invalid range here.
-- They should be handled by dedicated required-field checks
-- if those fields are required.
-- ============================================================

SELECT
    'VALIDATION FAILURE: invalid date range' AS validation_check,
    run_key,
    from_ts_utc,
    to_ts_utc
FROM backtest_runs
WHERE from_ts_utc IS NOT NULL
  AND to_ts_utc IS NOT NULL
  AND to_ts_utc <= from_ts_utc;


-- ============================================================
-- 12. NEGATIVE FEE RATE
--
-- NULL is treated as missing, not negative.
-- ============================================================

SELECT
    'VALIDATION FAILURE: negative fee rate' AS validation_check,
    run_key,
    fee_rate
FROM backtest_runs
WHERE fee_rate IS NOT NULL
  AND fee_rate < 0;


-- ============================================================
-- 13. NON-POSITIVE QUANTITY
-- ============================================================

SELECT
    'VALIDATION FAILURE: non-positive quantity' AS validation_check,
    run_key,
    seq,
    quantity
FROM backtest_trades
WHERE quantity IS NULL
   OR quantity <= 0;


-- ============================================================
-- 14. NON-POSITIVE FILL PRICE
-- ============================================================

SELECT
    'VALIDATION FAILURE: non-positive fill price' AS validation_check,
    run_key,
    seq,
    fill_price
FROM backtest_trades
WHERE fill_price IS NULL
   OR fill_price <= 0;


-- ============================================================
-- 15. MISSING INITIAL CASH
-- ============================================================

SELECT
    'VALIDATION FAILURE: missing initial cash' AS validation_check,
    run_key
FROM backtest_runs
WHERE initial_cash IS NULL;


-- ============================================================
-- 16. MISSING METRIC INITIAL CASH
-- ============================================================

SELECT
    'VALIDATION FAILURE: missing metric initial cash' AS validation_check,
    run_key
FROM backtest_runs
WHERE metric_initial_cash IS NULL;


-- ============================================================
-- 17. MISSING REPORTED TRADE COUNT
-- ============================================================

SELECT
    'VALIDATION FAILURE: missing reported trade count' AS validation_check,
    run_key
FROM backtest_runs
WHERE reported_num_trades IS NULL;


-- ============================================================
-- 18. MISSING TOTAL FEES
-- ============================================================

SELECT
    'VALIDATION FAILURE: missing total fees' AS validation_check,
    run_key
FROM backtest_runs
WHERE total_fees IS NULL;


-- ============================================================
-- 19. MISSING FINAL EQUITY
-- ============================================================

SELECT
    'VALIDATION FAILURE: missing final equity' AS validation_check,
    run_key
FROM backtest_runs
WHERE final_equity IS NULL;


-- ============================================================
-- 20. MISSING TIMEFRAME
-- ============================================================

SELECT
    'VALIDATION FAILURE: missing timeframe' AS validation_check,
    run_key
FROM backtest_runs
WHERE timeframe IS NULL;


-- ============================================================
-- 21. MISSING NUM_BARS_PROCESSED
-- ============================================================

SELECT
    'VALIDATION FAILURE: missing num bars processed' AS validation_check,
    run_key
FROM backtest_runs
WHERE num_bars_processed IS NULL;


-- ============================================================
-- 22. MISSING TOTAL RETURN
-- ============================================================

SELECT
    'VALIDATION FAILURE: missing total return' AS validation_check,
    run_key
FROM backtest_runs
WHERE total_return_pct IS NULL;


-- ============================================================
-- 23. NON-POSITIVE METRIC INITIAL CASH
--
-- NULL is handled by check #16.
-- ============================================================

SELECT
    'VALIDATION FAILURE: non-positive metric initial cash' AS validation_check,
    run_key,
    metric_initial_cash
FROM backtest_runs
WHERE metric_initial_cash IS NOT NULL
  AND metric_initial_cash <= 0;


-- ============================================================
-- 24. ZERO ACTUAL TRADES
--
-- Informational only.
-- A run can legitimately contain zero fills.
-- ============================================================

SELECT
    'INFORMATIONAL: run has zero actual trade rows' AS validation_check,
    r.run_key
FROM backtest_runs r
LEFT JOIN backtest_trades t
    ON r.run_key = t.run_key
GROUP BY r.run_key
HAVING COUNT(t.run_key) = 0;


-- ============================================================
-- 25. NEGATIVE FITNESS
-- ============================================================

SELECT
    'VALIDATION FAILURE: negative fitness' AS validation_check,
    candidate_key,
    fitness
FROM candidates
WHERE fitness IS NOT NULL
  AND fitness < 0;


-- ============================================================
-- 26. GLOBAL RETURN SUMMARY
--
-- Informational aggregate.
-- ============================================================

SELECT
    'INFORMATIONAL: global return summary' AS validation_check,
    MAX(total_return_pct) AS max_return_pct,
    AVG(total_return_pct) AS avg_return_pct,
    MIN(total_return_pct) AS min_return_pct
FROM backtest_runs
WHERE total_return_pct IS NOT NULL;


-- ============================================================
-- 27. INVALID NUMBER OF PROCESSED BARS
-- ============================================================

SELECT
    'VALIDATION FAILURE: invalid num bars processed' AS validation_check,
    run_key,
    num_bars_processed
FROM backtest_runs
WHERE num_bars_processed IS NULL
   OR num_bars_processed <= 0;


-- ============================================================
-- 28. UNSUPPORTED TIMEFRAME
--
-- Supported crypto annualization factors are defined below.
-- NULL is also surfaced here because annualization cannot be
-- validated without a timeframe.
-- ============================================================

SELECT
    'VALIDATION FAILURE: unsupported timeframe' AS validation_check,
    run_key,
    timeframe
FROM backtest_runs
WHERE timeframe IS NULL
   OR timeframe NOT IN (
       '1m',
       '3m',
       '5m',
       '15m',
       '30m',
       '1h',
       '2h',
       '4h',
       '6h',
       '8h',
       '12h',
       '1d',
       '3d',
       '1w',
       '1M'
   );


-- ============================================================
-- 29. NON-CRYPTO ANNUALIZATION
--
-- Within this validation scope, binance is the crypto venue
-- treated as 24/7. Other venues require the applicable
-- exchange-calendar factor.
--
-- Without a supplied exchange-calendar factor, this check is
-- informational rather than a failure.
-- ============================================================

SELECT
    'INFORMATIONAL: non-crypto annualization uncheckable without exchange-calendar factor'
        AS validation_check,
    run_key,
    venue,
    timeframe,
    num_bars_processed,
    total_return_pct,
    annualized_return_pct,
    'exchange-calendar annualization factor required'
        AS validation_status
FROM backtest_runs
WHERE LOWER(COALESCE(venue, '')) <> 'binance'
  AND num_bars_processed > 0
  AND total_return_pct IS NOT NULL
  AND annualized_return_pct IS NOT NULL;


-- ============================================================
-- 30. CRYPTO ANNUALIZED RETURN MISMATCH
--
-- Within this validation scope, binance is the crypto venue
-- treated as 24/7.
--
-- Annualization convention:
--
-- annualized_return_pct =
--     total_return_pct
--     * annualization_factor
--     / num_bars_processed
--
-- This is linear annualization, not CAGR.
--
-- Factors assume crypto trades 24/7:
-- ============================================================

SELECT
    'VALIDATION FAILURE: annualized return mismatch' AS validation_check,
    r.run_key,
    r.venue,
    r.timeframe,
    r.num_bars_processed,
    r.total_return_pct,
    r.annualized_return_pct,
    ROUND(
        r.total_return_pct *
        af.annualization_factor /
        (r.num_bars_processed * 1.0),
        4
    ) AS calculated_annualized_return_pct
FROM backtest_runs r
JOIN (
    SELECT '1m' AS timeframe, 525600 AS annualization_factor
    UNION ALL SELECT '3m', 175200
    UNION ALL SELECT '5m', 105120
    UNION ALL SELECT '15m', 35040
    UNION ALL SELECT '30m', 17520
    UNION ALL SELECT '1h', 8760
    UNION ALL SELECT '2h', 4380
    UNION ALL SELECT '4h', 2190
    UNION ALL SELECT '6h', 1460
    UNION ALL SELECT '8h', 1095
    UNION ALL SELECT '12h', 730
    UNION ALL SELECT '1d', 365
    UNION ALL SELECT '3d', 121
    UNION ALL SELECT '1w', 52
    UNION ALL SELECT '1M', 12
) af
    ON r.timeframe = af.timeframe
WHERE LOWER(COALESCE(r.venue, '')) = 'binance'
  AND r.num_bars_processed > 0
  AND r.total_return_pct IS NOT NULL
  AND r.annualized_return_pct IS NOT NULL
  AND ABS(
      r.annualized_return_pct -
      (
          r.total_return_pct *
          af.annualization_factor /
          (r.num_bars_processed * 1.0)
      )
  ) > 0.000001;
