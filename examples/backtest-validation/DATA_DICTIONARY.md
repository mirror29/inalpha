# Data Dictionary

## candidates

| Field | Meaning |
|---|---|
| candidate_key | Candidate identifier / primary key within the snapshot |
| status | Candidate status |
| author | Candidate origin: LLM/user/system |
| fitness | Candidate fitness; nullable by design |
| last_backtest_run_key | Soft reference to latest run; nullable by design |
| created_at_utc | Creation timestamp |
| updated_at_utc | Update timestamp |

## backtest_runs

| Field | Meaning |
|---|---|
| run_key | Backtest run identifier |
| candidate_key | Associated candidate |
| strategy_kind | Strategy type |
| status | Run status |
| venue | Venue |
| symbol | Instrument |
| timeframe | Observation frequency |
| from_ts_utc / to_ts_utc | Historical test window |
| initial_cash | Requested/configured initial cash |
| metric_initial_cash | Portfolio-report initial cash; authoritative for independent total-return validation |
| fee_rate | Configured fee rate |
| params_json | Run parameters |
| num_bars_processed | Number of processed bars |
| reported_num_trades | Reported fill count in this dataset |
| final_equity | Final portfolio equity |
| total_return_pct | Total return percentage |
| annualized_return_pct | Linear-annualized return |
| annualized_volatility_pct | Engine-reported annualized volatility |
| max_drawdown_pct | Engine-reported maximum drawdown |
| sharpe / sortino / calmar | Risk-adjusted metrics |
| win_rate / profit_factor / payoff_ratio / expectancy | Run performance metrics |
| exposure_pct | Reported exposure |
| total_fees | Total fill-level fees |
| best_trade_pnl / worst_trade_pnl | Trade extremes |
| max_consecutive_wins / max_consecutive_losses | Run streak metrics |
| max_drawdown_duration_bars | Engine-reported drawdown duration |
| fitness | Run fitness |
| validation_json | Validation metadata |
| started_at_utc / finished_at_utc / created_at_utc | Run timestamps |

## backtest_trades

| Field | Meaning |
|---|---|
| run_key | Parent backtest run |
| seq | Fill sequence within run |
| bar_ts_utc | Fill bar timestamp |
| bar_close | Bar close |
| side | Fill side |
| quantity | Fill quantity |
| order_type | Order type |
| fill_price | Simulated fill price |
| fee | Fill-level fee |
| realized_pnl | Gross realized P&L; fee is separate |
| intent | Fill/order intent |

`_utc` fields are UTC timestamps. A fill is one simulated execution event; multiple fills can form a round trip.
