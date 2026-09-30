## Why Gross Realized P&L Is the Primary Custom Measure

The Power BI prototype uses gross realized P&L as its primary custom analytical
measure:

```DAX
Total Gross Realized P&L =
SUM(backtest_trades[realized_pnl])
```

This choice follows the grain of the analytical model.

`backtest_trades[realized_pnl]` is recorded at the individual fill/trade level.
Summing this field provides the gross realized P&L represented by the
underlying simulated fills.

This makes it particularly suitable as the starting value for the
decomposition-tree analysis:

```text
Total Gross Realized P&L
        ↓
candidate_key
        ↓
run_key
        ↓
symbol
        ↓
timeframe
```

The decomposition therefore answers an analytical question such as:

> Where is the realized P&L coming from?

rather than attempting to reconstruct the complete backtest performance model
inside Power BI.

---

## Why Other Performance Metrics Are Not Reimplemented as DAX

The underlying `backtest_runs` table already contains run-level performance
metrics such as:

- `total_return_pct`
- `annualized_return_pct`
- `annualized_volatility_pct`
- `max_drawdown_pct`
- `sharpe`
- `sortino`
- `calmar`
- `win_rate`
- `profit_factor`
- `payoff_ratio`
- `expectancy`
- `exposure_pct`
- `fitness`

These values have already been produced at the backtest-run level.

The SQL validation layer independently checks the metrics for which the
validation specification provides reproducible calculations.

The Power BI prototype therefore does not recreate these metrics through
additional DAX measures.

Doing so would duplicate the analytical/validation logic already represented
in the source data and SQL layer without being necessary for the current
exploration workflow.

---

## Why Realized P&L Is Different

Realized P&L is particularly useful as the Power BI starting metric because it
exists at the same underlying grain as the trade/fill data being explored.

For a selected run, the dashboard can move from:

```text
Aggregate realized P&L
        ↓
Run
        ↓
Underlying fills
        ↓
Realized P&L / fee / price sequence
```

This makes the measure useful both for high-level decomposition and for
lower-level drill-through analysis.

The measure is therefore not intended to represent every aspect of backtest
performance. It is the primary analytical measure used to trace the realized
trading result through the underlying candidate/run/trade structure.

---

## Selective DAX Rather Than Complete Metric Recreation

The prototype intentionally uses DAX selectively.

A custom measure is introduced when a specific analytical definition is useful
to the report.

Simple numeric fields can instead use Power BI's native aggregation behavior,
while run-level metrics can be displayed directly from their source fields.

This avoids creating a separate DAX implementation for every metric merely
because Power BI supports custom measures.

If a future requirement calls for a specific derived metric to be calculated
inside Power BI, that measure can be added explicitly and documented from the
final PBIX/model.
