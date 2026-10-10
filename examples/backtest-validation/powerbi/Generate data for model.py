import pandas as pd
from datetime import datetime, timedelta
import json

def generate_powerbi_dataset():
    # 1. Clean Instrument / Venue / Timeframe Mapping
    configs = [
        {"symbol": "BTC/USDT", "timeframe": "1h", "venue": "binance", "strategy_kind": "trend_following"},
        {"symbol": "ETH/USDT", "timeframe": "4h", "venue": "binance", "strategy_kind": "mean_reversion"},
        {"symbol": "SOL/USDT", "timeframe": "1d", "venue": "binance", "strategy_kind": "breakout"},
        {"symbol": "AAPL", "timeframe": "1d", "venue": "baostock", "strategy_kind": "stat_arb"},
        {"symbol": "MSFT", "timeframe": "1d", "venue": "baostock", "strategy_kind": "mean_reversion"}
    ]

    candidates = []
    runs = []
    trades = []

    global_run_counter = 1
    
    # Generate exactly 5 Candidates
    for c_idx in range(1, 6):
        candidate_key = f'C{c_idx:03d}'
        
        c_status = 'active' if c_idx % 2 != 0 else 'archived'
        
        candidates.append({
            'candidate_key': candidate_key,
            'status': c_status,
            'author': f'algo_dev_{c_idx}',
            'fitness': 1.0 + (c_idx * 0.25),
            'last_backtest_run_key': '', # Will be updated after runs are generated
            'created_at_utc': '2023-01-01T10:00:00Z',
            'updated_at_utc': '2023-01-02T10:00:00Z'
        })
        
        # Generate exactly 2 Runs per Candidate (10 Runs total)
        for r_idx in range(1, 3):
            run_key = f'R{global_run_counter:03d}'
            # Update the candidate's pointer to the latest run
            candidates[-1]['last_backtest_run_key'] = run_key 
            
            # Deterministic config cycling
            config = configs[(global_run_counter - 1) % len(configs)]
            
            initial_cash = 10000.00 + (global_run_counter * 1000.00)
            num_trades = 10 # Exactly 10 trades per run
            
            run_trades = []
            run_fees = 0.0
            
            # Runs start 15 days apart
            run_start_date = datetime(2023, 1, 1, 0, 0, 0) + timedelta(days=(global_run_counter - 1) * 15)
            
            # P&L deterministic repeating pattern for exits
            pnl_pattern = [28.0, 42.0, -18.0, 35.0, 15.0]
            
            # Generate exactly 10 Trades per Run (5 entries, 5 exits)
            for seq in range(1, num_trades + 1):
                # Trades spaced 1 day apart
                trade_dt = run_start_date + timedelta(days=seq - 1)
                bar_ts_utc = trade_dt.strftime('%Y-%m-%dT%H:%M:%SZ')
                
                is_entry = (seq % 2 != 0)
                intent = 'entry' if is_entry else 'exit'
                side = 'buy' if is_entry else 'sell'
                
                # Deterministic prices and sizes
                bar_close = 100.0 + (global_run_counter * 10) + seq
                qty = 0.5 + (seq * 0.1)
                fill_price = bar_close
                fee = round(qty * fill_price * 0.001, 2)
                
                # Deterministic P&L logic: 0 on entry, mixture on exit
                if is_entry:
                    realized_pnl = 0.0
                else:
                    exit_idx = (seq // 2) - 1
                    base_pnl = pnl_pattern[exit_idx % len(pnl_pattern)]
                    # Scale slightly by run number for variation
                    realized_pnl = base_pnl * (1.0 + (global_run_counter * 0.05))
                    
                realized_pnl = round(realized_pnl, 2)
                
                run_trades.append({
                    'run_key': run_key,
                    'seq': seq,
                    'bar_ts_utc': bar_ts_utc,
                    'bar_close': bar_close,
                    'side': side,
                    'quantity': round(qty, 4),
                    'order_type': 'limit' if seq % 3 == 0 else 'market',
                    'fill_price': fill_price,
                    'fee': fee,
                    'realized_pnl': realized_pnl,
                    'intent': intent
                })
                
                run_fees += fee
                
            trades.extend(run_trades)
            
            # Run roll-up calculations derived strictly from the generated trade rows
            # final_equity = initial_cash + total gross realized P&L - total fees
            total_pnl = sum([t['realized_pnl'] for t in run_trades])
            final_eq = initial_cash + total_pnl - run_fees
            ret_pct = (final_eq - initial_cash) / initial_cash * 100
            
            # Metrics based on actual non-zero realized_pnl values
            all_realized = [t['realized_pnl'] for t in run_trades if t['realized_pnl'] != 0]
            winning_trades = [p for p in all_realized if p > 0]
            
            win_rate = len(winning_trades) / len(all_realized) if all_realized else 0.0
            best_pnl = max(all_realized) if all_realized else 0.0
            worst_pnl = min(all_realized) if all_realized else 0.0
            
            # Fix Run-Duration Inconsistency
            from_dt = datetime.strptime(run_trades[0]['bar_ts_utc'], '%Y-%m-%dT%H:%M:%SZ')
            to_dt = datetime.strptime(run_trades[-1]['bar_ts_utc'], '%Y-%m-%dT%H:%M:%SZ')
            run_duration_days = (to_dt - from_dt).total_seconds() / 86400.0
            if run_duration_days <= 0:
                run_duration_days = 1.0 # Safety fallback to avoid division by zero
                
            # Synthetic annualization calculation (not intended to imply universal venue conventions)
            annualization_factor = 365.0 if config['venue'] == 'binance' else 252.0
            annualized_return_pct = ret_pct * (annualization_factor / run_duration_days)
            
            runs.append({
                'run_key': run_key,
                'candidate_key': candidate_key,
                'strategy_kind': config['strategy_kind'],
                'status': 'completed',
                'venue': config['venue'],
                'symbol': config['symbol'],
                'timeframe': config['timeframe'],
                'from_ts_utc': run_trades[0]['bar_ts_utc'],
                'to_ts_utc': run_trades[-1]['bar_ts_utc'],
                'initial_cash': initial_cash,
                'fee_rate': 0.001,
                'params_json': json.dumps({"risk": 0.02, "sl": 0.05}),
                'num_bars_processed': 1000 + (global_run_counter * 100),
                'reported_num_trades': num_trades,
                'metric_initial_cash': initial_cash,
                'final_equity': round(final_eq, 2),
                'total_return_pct': round(ret_pct, 4),
                
                # -------------------------------------------------------------------------
                # NOTE: The following fields are synthetic representative values for the 
                # clean Power BI fixture. They are NOT independently reconstructed from an 
                # equity curve and are NOT intended to serve as independent validation evidence.
                # -------------------------------------------------------------------------
                'annualized_return_pct': round(annualized_return_pct, 4),
                'annualized_volatility_pct': 15.0 + global_run_counter,
                'max_drawdown_pct': -5.0 - (global_run_counter * 0.5),
                'sharpe': round(1.0 + (global_run_counter * 0.1), 2),
                'sortino': round(1.2 + (global_run_counter * 0.1), 2),
                'calmar': round(0.8 + (global_run_counter * 0.1), 2),
                'win_rate': round(win_rate, 4),
                'profit_factor': round(1.5 + (global_run_counter * 0.05), 2),
                'payoff_ratio': round(1.2 + (global_run_counter * 0.05), 2),
                'expectancy': round(5.0 + global_run_counter, 2),
                'exposure_pct': round(45.0 + (global_run_counter * 2.0), 2),
                'total_fees': round(run_fees, 2),
                'best_trade_pnl': best_pnl,
                'worst_trade_pnl': worst_pnl,
                'max_consecutive_wins': 3,
                'max_consecutive_losses': 1,
                'max_drawdown_duration_bars': 20 + global_run_counter,
                'fitness': round(1.0 + (global_run_counter * 0.2), 2),
                
                'validation_json': json.dumps({"passed": True}),
                'created_at_utc': '2023-01-01T10:00:00Z',
                'updated_at_utc': '2023-01-02T10:00:00Z'
            })
            
            global_run_counter += 1

    # Convert to pandas DataFrames
    c_df = pd.DataFrame(candidates)
    r_df = pd.DataFrame(runs)
    t_df = pd.DataFrame(trades)

    # ==================================================
    # Automated Validation Section
    # ==================================================
    
    # 1. candidate_key uniqueness
    assert c_df['candidate_key'].is_unique, "Validation Failed: candidate_key is not unique."
    
    # 2. run_key uniqueness
    assert r_df['run_key'].is_unique, "Validation Failed: run_key is not unique."
    
    # 3. candidate foreign-key integrity
    assert set(r_df['candidate_key']).issubset(set(c_df['candidate_key'])), "Validation Failed: Orphan backtest_runs found."
    
    # 4. trade run_key foreign-key integrity
    assert set(t_df['run_key']).issubset(set(r_df['run_key'])), "Validation Failed: Orphan backtest_trades found."
    
    # 5. (run_key, seq) uniqueness
    assert t_df.duplicated(subset=['run_key', 'seq']).sum() == 0, "Validation Failed: (run_key, seq) is not unique."
    
    # 6 & 7. Every run has exactly 10 trades & reported_num_trades equals actual trade count
    trade_counts = t_df.groupby('run_key').size()
    for _, run_row in r_df.iterrows():
        r_key = run_row['run_key']
        actual_count = trade_counts.get(r_key, 0)
        assert actual_count == 10, f"Validation Failed: {r_key} has {actual_count} trades instead of 10."
        assert run_row['reported_num_trades'] == actual_count, f"Validation Failed: {r_key} reported_num_trades does not match actual count."

    # 8. last_backtest_run_key resolves to an existing run belonging to that candidate
    for _, cand_row in c_df.iterrows():
        c_key = cand_row['candidate_key']
        last_r_key = cand_row['last_backtest_run_key']
        assert last_r_key in r_df['run_key'].values, f"Validation Failed: last_backtest_run_key {last_r_key} for {c_key} does not exist."
        
        # Verify it actually belongs to the candidate that owns it
        cand_runs = r_df[r_df['candidate_key'] == c_key]['run_key'].values
        assert last_r_key in cand_runs, f"Validation Failed: {last_r_key} does not belong to candidate {c_key}."

    # 9. Required Power BI fields contain no nulls
    assert c_df.notnull().all().all(), "Validation Failed: Null values detected in candidates."
    assert r_df.notnull().all().all(), "Validation Failed: Null values detected in backtest_runs."
    assert t_df.notnull().all().all(), "Validation Failed: Null values detected in backtest_trades."
    
    # 10. All generated CSVs have the expected row counts
    assert len(c_df) == 5, f"Validation Failed: Expected 5 candidates, got {len(c_df)}"
    assert len(r_df) == 10, f"Validation Failed: Expected 10 runs, got {len(r_df)}"
    assert len(t_df) == 100, f"Validation Failed: Expected 100 trades, got {len(t_df)}"

    # Write DataFrames directly to CSV
    c_df.to_csv("candidates.csv", index=False)
    r_df.to_csv("backtest_runs.csv", index=False)
    t_df.to_csv("backtest_trades.csv", index=False)

    print("Power BI synthetic dataset generated successfully.\n")
    print("candidates.csv: 5 rows")
    print("backtest_runs.csv: 10 rows")
    print("backtest_trades.csv: 100 rows\n")
    print("All relational integrity checks passed.")

if __name__ == "__main__":
    generate_powerbi_dataset()
