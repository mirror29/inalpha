# Relationship Map

```text
candidates
  candidate_key (PK)
       |
       | 1:N
       v
backtest_runs
  run_key (PK)
  candidate_key (FK)
       |
       | 1:N
       v
backtest_trades
  (run_key, seq) (PK)
  run_key (FK)
```

`candidates.last_backtest_run_key` is a soft reference to the latest run. It is not the primary candidate-to-run relationship and should not create a second active lineage path.


For the clean Power BI fixture, activate single-direction filters from candidates
to runs and from runs to fills. Add an active 1:N relationship from
`backtest_runs[run_key]` to `validation_exceptions[run_key]`, also filtering from
runs to exceptions. A candidate is one candidate row, a run is one backtest row,
and a fill is one `(run_key, seq)` execution event. The separate SQLite fixture
intentionally violates these keys to exercise the validation queries and must not
be imported as this clean relationship model.
