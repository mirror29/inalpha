# Power Query M

// Replace the path below with the local path to the folder containing the sanitized CSV files.


# Candidates M code:

let
    Source = Csv.Document(File.Contents("Your local path\candidates.csv"),[Delimiter=",", Columns=7, Encoding=1252, QuoteStyle=QuoteStyle.None]),
    #"Promoted Headers" = Table.PromoteHeaders(Source, [PromoteAllScalars=true]),
    #"Changed Type" = Table.TransformColumnTypes(#"Promoted Headers",{{"candidate_key", type text}, {"status", type text}, {"author", type text}, {"fitness", type number}, {"last_backtest_run_key", type text}, {"created_at_utc", type datetime}, {"updated_at_utc", type datetime}}),
    #"Replaced Value" = Table.ReplaceValue(#"Changed Type","","not yet done",Replacer.ReplaceValue,{"last_backtest_run_key"}),
    #"Replaced Value1" = Table.ReplaceValue(#"Replaced Value","not yet done","",Replacer.ReplaceText,{"last_backtest_run_key"})
in
    #"Replaced Value1"

# Backtest_trades M code:

let
    Source = Csv.Document(File.Contents("Your local path\backtest_trades.csv"),[Delimiter=",", Columns=11, Encoding=1252, QuoteStyle=QuoteStyle.None]),
    #"Promoted Headers" = Table.PromoteHeaders(Source, [PromoteAllScalars=true]),
    #"Changed Type" = Table.TransformColumnTypes(#"Promoted Headers",{{"run_key", type text},
    {"seq", Int64.Type},
    {"bar_ts_utc", type datetime},
    {"bar_close", type number},
    {"side", type text},
    {"quantity", type number},
    {"order_type", type text},
    {"fill_price", type number},
    {"fee", type number},
    {"realized_pnl", type number}, 
    {"intent", type text}})
in
    #"Changed Type"

# backtest_runs m code:

let
    Source = Csv.Document(File.Contents("Your local path\backtest_runs.csv"),[Delimiter=",", Columns=39, Encoding=1252, QuoteStyle=QuoteStyle.None]),
    #"Promoted Headers" = Table.PromoteHeaders(Source, [PromoteAllScalars=true]),
    #"Changed Type" = Table.TransformColumnTypes(#"Promoted Headers",{{"run_key", type text}, 
    {"candidate_key", type text},
    {"strategy_kind", type text},
    {"status", type text}, 
    {"venue", type text},
    {"symbol", type text},
    {"timeframe", type text}, 
    {"from_ts_utc", type datetime},
    {"to_ts_utc", type datetime}, 
    {"initial_cash", type number}, 
    {"fee_rate", type number},
    {"params_json", type text},
    {"num_bars_processed", Int64.Type},
    {"reported_num_trades", Int64.Type}, 
    {"metric_initial_cash", type number},
    {"final_equity", type number}, 
    {"total_return_pct", type number},
    {"annualized_return_pct", type number},
    {"annualized_volatility_pct", type number},
    {"max_drawdown_pct", type number},
    {"sharpe", type number},
    {"sortino", type number},
    {"calmar", type number},
    {"win_rate", type number},
    {"profit_factor", type number},
    {"payoff_ratio", type number},
    {"expectancy", type number},
    {"exposure_pct", type number},
    {"total_fees", type number},
    {"best_trade_pnl", type number},
    {"worst_trade_pnl", type number},
    {"max_consecutive_wins", Int64.Type},
    {"max_consecutive_losses", Int64.Type},
    {"max_drawdown_duration_bars", Int64.Type},
    {"fitness", type number},
    {"validation_json", type text}, 
    {"started_at_utc", type datetime},
    {"finished_at_utc", type datetime},
    {"created_at_utc", type datetime}})
in
    #"Changed Type"
