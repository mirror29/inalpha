# Inalpha — Metric Catalogue

This catalogue documents the stored backtest metrics, their calculation conventions, source fields, edge-case behavior, and reproducibility from the available candidate → backtest run → fill dataset.

## Metric Catalogue

| Metric | Formula / Definition | Unit | Source Fields | Grain | NULL / Edge Behavior | Fee Treatment | Reproducibility |
|---|---|---|---|---|---|---|---|
| Total return | `(final_equity - metric_initial_cash) / metric_initial_cash * 100` | % | `final_equity`, `metric_initial_cash` | Run | Requires non-NULL `metric_initial_cash` and non-zero denominator | Reflected in `final_equity`; `realized_pnl` itself is gross | Independently validated |
| Annualized return | `years = num_bars_processed / annualization_periods`; `annualized_return_pct = total_return_pct / years` | % annualized | `total_return_pct`, `num_bars_processed`, `timeframe` | Run | Requires positive bar count and supported timeframe; unsupported/missing timeframe is uncheckable | Inherited from total return | Independently validated against confirmed engine convention |
| Total fees | `SUM(fee)` across fills for the run | Currency | `backtest_trades.fee` | Run | Requires fill-level fee values to reconcile; zero-fill runs may have zero fees | Fees are included directly | Independently reconciled |
| Gross realized P&L | `SUM(realized_pnl)` across fills for the run | Currency | `backtest_trades.realized_pnl` | Run | Depends on available fill-level realized P&L values | Fees are separate and are not subtracted from `realized_pnl` | Fill-derived |
| Reported trade count | Stored run-level count compared with actual fill count | Count | `reported_num_trades`, `backtest_trades.run_key` | Run | NULL reported count is invalid for required-field validation | Not applicable | Independently reconciled for this dataset |
| Sharpe | Mean per-bar simple excess return / sample standard deviation × √annualization factor | Ratio | Engine-reported metric; per-bar equity returns | Run | Cannot be independently reconstructed without the per-bar equity curve | Depends on the engine's equity path, including applicable fees | Engine-reported |
| Sortino | Mean per-bar return above target 0 / downside deviation × √annualization factor | Ratio | Engine-reported metric; per-bar equity returns | Run | Cannot be independently reconstructed without the per-bar equity curve | Depends on the engine's equity path, including applicable fees | Engine-reported |
| Annualized volatility | Sample standard deviation of per-bar returns × √annualization factor × 100 | % annualized | Engine-reported metric; per-bar equity returns | Run | Cannot be independently reconstructed without the per-bar equity curve | Depends on the engine's equity path, including applicable fees | Engine-reported |
| Maximum drawdown | Maximum percentage decline from a prior equity peak | % | Engine-reported metric; per-bar equity path | Run | Requires the complete per-bar equity path | Depends on equity values used by the engine | Engine-reported |
| Maximum drawdown duration | Longest number of bars spent below the previous equity peak | Bars | Engine-reported metric; per-bar equity path | Run | Requires the complete per-bar equity path | Depends on equity values used by the engine | Engine-reported |
| Calmar | Annualized return / absolute maximum drawdown | Ratio | `annualized_return_pct`, `max_drawdown_pct` | Run | Can use stored annualized return and drawdown, but underlying drawdown path cannot be reconstructed | Inherited from component metrics | Partially reproducible |
| Win rate | Winning completed trades / total completed trades | % | Fill sequence and realized P&L | Run | Depends on identifying completed trades and round-trip closure semantics | Depends on whether fees are included in trade-level aggregation | Partially reproducible |
| Profit factor | Gross winning trade P&L / absolute gross losing trade P&L | Ratio | Fill sequence and realized P&L | Run | Depends on trade aggregation and completed-trade semantics; undefined when there are no losses | Depends on trade aggregation convention | Partially reproducible |
| Payoff ratio | Average winning trade P&L / absolute average losing trade P&L | Ratio | Fill sequence and realized P&L | Run | Undefined when there are no winning or losing trades | Depends on trade aggregation convention | Partially reproducible |
| Expectancy | Average P&L per completed trade under the engine's trade aggregation convention | Currency per trade | Fill sequence and realized P&L | Run | Depends on completed-trade identification and aggregation semantics | Depends on whether fees are included in the trade-level calculation | Partially reproducible |
| Exposure | Time/position exposure calculated from the run's position state | % | Fill ordering, quantities, bar sequence | Run | Cannot be uniquely reconstructed without confirmed netting and fill-order semantics | Depends on the position/equity convention used by the engine | Partially reproducible |

## Cash and Return Conventions

### `initial_cash`

Configured/requested starting cash for the backtest.

### `metric_initial_cash`

Portfolio-report starting cash used by the engine's metric calculations. This is the authoritative starting value for independently validating `total_return_pct`.

### `final_equity`

Final portfolio equity reported by the backtest run.

### Total return

The independently validated identity is:

`(final_equity - metric_initial_cash) / metric_initial_cash * 100`

A non-positive `metric_initial_cash` is treated as an invalid denominator and is not considered a successful reconciliation.

## Annualized Return Convention

Annualized return uses **linear annualization, not CAGR**.

The convention is:

`years = num_bars_processed / annualization_periods`

`annualized_return_pct = total_return_pct / years`

Equivalent form:

`annualized_return_pct = total_return_pct * annualization_periods / num_bars_processed`

Annualization therefore requires:

- a positive `num_bars_processed`
- a supported timeframe
- the appropriate annualization factor for the market/timeframe convention

Missing or unsupported timeframe values are treated as uncheckable rather than silently passing validation.

## Crypto Annualization Factors

| Timeframe | Bars/year |
|---|---:|
| 1m | 525600 |
| 3m | 175200 |
| 5m | 105120 |
| 15m | 35040 |
| 30m | 17520 |
| 1h | 8760 |
| 2h | 4380 |
| 4h | 2190 |
| 6h | 1460 |
| 8h | 1095 |
| 12h | 730 |
| 1d | 365 |
| 3d | 121 |
| 1w | 52 |
| 1M | 12 |

Non-crypto markets require the applicable exchange/session calendar convention and are not assumed to use the crypto factors above.

## Fee Convention

Fill-level `fee` values are summed to independently reconcile `total_fees`.

`realized_pnl` is gross realized P&L from the fill records and does **not** include the fill-level fee. Fees are therefore kept as a separate metric.

## Reproducibility Classes

### Independently validated

The metric can be recalculated from persisted fields using the documented engine convention and compared directly with the stored value.

### Independently reconciled

The stored run-level value can be compared against an aggregation of persisted lower-grain records.

### Fill-derived

The metric can be derived directly from the persisted fill-level data, subject to the documented definition.

### Partially reproducible

Some inputs are available, but the result depends on additional engine semantics such as trade aggregation, fill ordering, or position netting.

### Engine-reported

The metric depends on the engine's per-bar equity path or other data that is not persisted in the supplied dataset and therefore cannot currently be independently reconstructed.

## Full-Validation Gap

The supplied dataset does not contain a per-bar equity curve.

A minimum additional persisted dataset for independent reconstruction of the equity-path metrics would contain:

`run_key, bar_ts_utc, equity`

An optional `exposure` field would also support more complete exposure validation.

Persisting the equity curve would enable independent reconstruction of:

- Sharpe
- Sortino
- annualized volatility
- maximum drawdown
- maximum drawdown duration

Calmar could then also be independently validated from the reconstructed drawdown path and annualized return.

The absence of the equity curve is therefore a reproducibility limitation of the supplied dataset, not evidence that the engine's reported values are incorrect.
