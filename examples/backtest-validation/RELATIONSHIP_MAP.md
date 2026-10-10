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
