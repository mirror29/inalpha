-- Power BI synthetic fixture only: Equity Consistency Check
-- Validates final_equity against realized P&L and fees.
-- Scoped strictly to the synthetic fixture's assumption of zero unrealized P&L.
SELECT
    r.run_key,
    'equity_consistency_check' AS check_name,
    'failed' AS status,
    'Equity mismatch detected: expected ' ||
    ROUND(r.initial_cash + COALESCE(SUM(t.realized_pnl), 0) - COALESCE(SUM(t.fee), 0), 2) ||
    ', actual ' || r.final_equity AS details
FROM backtest_runs r
LEFT JOIN backtest_trades t ON r.run_key = t.run_key
GROUP BY r.run_key
HAVING ROUND(r.initial_cash + COALESCE(SUM(t.realized_pnl), 0) - COALESCE(SUM(t.fee), 0), 2) <> r.final_equity;
