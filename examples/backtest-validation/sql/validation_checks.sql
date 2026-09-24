-- Inalpha Issue #165 — Strategy-to-backtest validation
-- Reproducible SQLite validation checks

SELECT candidate_key, COUNT(*) AS candidate_count FROM candidates GROUP BY candidate_key HAVING COUNT(*) > 1;
SELECT run_key, COUNT(*) AS run_count FROM backtest_runs GROUP BY run_key HAVING COUNT(*) > 1;
SELECT run_key, seq, COUNT(*) AS fill_count FROM backtest_trades GROUP BY run_key, seq HAVING COUNT(*) > 1;

SELECT c.candidate_key FROM candidates c LEFT JOIN backtest_runs r ON r.candidate_key = c.candidate_key WHERE r.run_key IS NULL;
SELECT r.run_key, r.candidate_key FROM backtest_runs r LEFT JOIN candidates c ON r.candidate_key = c.candidate_key WHERE c.candidate_key IS NULL;
SELECT t.run_key FROM backtest_trades t LEFT JOIN backtest_runs r ON t.run_key = r.run_key WHERE r.run_key IS NULL;

SELECT r.run_key, r.reported_num_trades, COUNT(t.run_key) AS actual_fill_count
FROM backtest_runs r LEFT JOIN backtest_trades t ON r.run_key = t.run_key
GROUP BY r.run_key, r.reported_num_trades
HAVING COUNT(t.run_key) <> r.reported_num_trades;

SELECT r.run_key, r.total_fees, COALESCE(SUM(t.fee), 0) AS calculated_fees
FROM backtest_runs r LEFT JOIN backtest_trades t ON r.run_key = t.run_key
GROUP BY r.run_key, r.total_fees
HAVING ABS(r.total_fees - COALESCE(SUM(t.fee), 0)) > 0.00000001;

SELECT run_key, initial_cash, metric_initial_cash, initial_cash - metric_initial_cash AS difference
FROM backtest_runs
WHERE ABS(initial_cash - metric_initial_cash) > 0.00000001;

SELECT 
    run_key, 
    total_return_pct,
    ((CAST(final_equity AS REAL) - CAST(metric_initial_cash AS REAL)) / CAST(metric_initial_cash AS REAL)) * 100.0 AS calculated_return_pct
FROM backtest_runs
WHERE ABS(total_return_pct - (((CAST(final_equity AS REAL) - CAST(metric_initial_cash AS REAL)) / CAST(metric_initial_cash AS REAL)) * 100.0)) > 0.000001;

SELECT run_key, from_ts_utc, to_ts_utc FROM backtest_runs WHERE to_ts_utc <= from_ts_utc;
SELECT run_key, fee_rate FROM backtest_runs WHERE fee_rate < 0;
SELECT * FROM backtest_trades WHERE quantity <= 0;
SELECT * FROM backtest_trades WHERE fill_price IS NOT NULL AND fill_price <= 0;

SELECT run_key, 'missing initial_cash' AS validation_error
FROM backtest_runs
WHERE initial_cash IS NULL;

SELECT run_key, 'missing total_return_pct' AS validation_error
FROM backtest_runs
WHERE total_return_pct IS NULL;

-- Required fields and invalid denominators

SELECT run_key, 'missing metric_initial_cash' AS validation_error
FROM backtest_runs
WHERE metric_initial_cash IS NULL;

SELECT run_key, 'non-positive metric_initial_cash' AS validation_error
FROM backtest_runs
WHERE metric_initial_cash <= 0;

SELECT run_key, 'missing reported_num_trades' AS validation_error
FROM backtest_runs
WHERE reported_num_trades IS NULL;

SELECT run_key, 'missing total_fees' AS validation_error
FROM backtest_runs
WHERE total_fees IS NULL;

SELECT run_key, 'missing final_equity' AS validation_error
FROM backtest_runs
WHERE final_equity IS NULL;

SELECT run_key, 'missing timeframe' AS validation_error
FROM backtest_runs
WHERE timeframe IS NULL;

SELECT run_key, 'missing num_bars_processed' AS validation_error
FROM backtest_runs
WHERE num_bars_processed IS NULL;
---     ---
SELECT r.run_key, r.reported_num_trades, r.total_return_pct, r.total_fees
FROM backtest_runs r LEFT JOIN backtest_trades t ON r.run_key = t.run_key
GROUP BY r.run_key, r.reported_num_trades, r.total_return_pct, r.total_fees
HAVING COUNT(t.run_key) = 0;

SELECT candidate_key, fitness FROM candidates WHERE fitness < 0;
SELECT MAX(total_return_pct) AS maximum_return, AVG(total_return_pct) AS average_return, MIN(total_return_pct) AS minimum_return FROM backtest_runs;

-- Annualization: invalid bar counts

SELECT
    run_key,
    'missing or non-positive num_bars_processed' AS validation_error
FROM backtest_runs
WHERE num_bars_processed IS NULL
   OR num_bars_processed <= 0;


-- Annualization: unsupported timeframe

SELECT
    run_key,
    timeframe,
    'unsupported timeframe for annualization' AS validation_error
FROM backtest_runs
WHERE timeframe IS NULL
   OR timeframe NOT IN (
       '1m', '3m', '5m', '15m', '30m',
       '1h', '2h', '4h', '6h', '8h', '12h',
       '1d', '3d', '1w', '1M'
   );


-- Annualization: reported value vs calculated value

SELECT
    run_key,
    timeframe,
    total_return_pct,
    annualized_return_pct,
    (
        total_return_pct *
        CASE timeframe
            WHEN '1m'  THEN 525600
            WHEN '3m'  THEN 175200
            WHEN '5m'  THEN 105120
            WHEN '15m' THEN 35040
            WHEN '30m' THEN 17520
            WHEN '1h'  THEN 8760
            WHEN '2h'  THEN 4380
            WHEN '4h'  THEN 2190
            WHEN '6h'  THEN 1460
            WHEN '8h'  THEN 1095
            WHEN '12h' THEN 730
            WHEN '1d'  THEN 365
            WHEN '3d'  THEN 121
            WHEN '1w'  THEN 52
            WHEN '1M'  THEN 12
        END
        / (num_bars_processed * 1.0)
    ) AS calculated_annualized_return_pct
FROM backtest_runs
WHERE num_bars_processed > 0
  AND total_return_pct IS NOT NULL
  AND annualized_return_pct IS NOT NULL
  AND timeframe IN (
      '1m', '3m', '5m', '15m', '30m',
      '1h', '2h', '4h', '6h', '8h', '12h',
      '1d', '3d', '1w', '1M'
  )
  AND ABS(
      annualized_return_pct -
      (
          total_return_pct *
          CASE timeframe
              WHEN '1m'  THEN 525600
              WHEN '3m'  THEN 175200
              WHEN '5m'  THEN 105120
              WHEN '15m' THEN 35040
              WHEN '30m' THEN 17520
              WHEN '1h'  THEN 8760
              WHEN '2h'  THEN 4380
              WHEN '4h'  THEN 2190
              WHEN '6h'  THEN 1460
              WHEN '8h'  THEN 1095
              WHEN '12h' THEN 730
              WHEN '1d'  THEN 365
              WHEN '3d'  THEN 121
              WHEN '1w'  THEN 52
              WHEN '1M'  THEN 12
          END
          / (num_bars_processed * 1.0)
      )
  ) > 0.000001;

