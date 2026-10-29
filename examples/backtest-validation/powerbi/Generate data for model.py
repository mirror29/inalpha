import os
import sys
import pandas as pd
from datetime import datetime, timedelta
import json

def generate_powerbi_dataset():
    output_dir = "fixture_output"
    
    # Overwrite protection
    if os.path.exists(output_dir):
        print(f"Error: The directory '{output_dir}' already exists.")
        print("Please delete or rename it to prevent overwriting existing data.")
        sys.exit(1)
        
    os.makedirs(output_dir)

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
            total_pnl = sum([t['realized_pnl'] for t in run_trades])
            expected_final_eq = initial_cash + total_pnl - run_fees
            final_eq = expected_final_eq
            
            # INJECT DELIBERATE EQUITY FAILURE FOR R002
            if run_key == "R002":
                final_eq += 50.0 
                
            ret_pct = (final_eq - initial_cash) / initial_cash * 100
            
            # Metrics based on actual non-zero realized_pnl values
            all_realized = [t['realized_pnl'] for t in run_trades if t['realized_pnl'] != 0]
            winning_trades = [p for p in all_realized if p > 0]
            
            win_rate = len(winning_trades) / len(all_realized) if all_realized else 0.0
            best_pnl = max(all_realized) if all_realized else 0.0
            worst_pnl = min(all_realized) if all_realized else 0.0
            
            # Inalpha's exact linear annualization math
            num_bars = 1000 + (global_run_counter * 100)
            
            if config['venue'] == 'binance':
                if config['timeframe'] == '1h':
                    bars_per_year = 365 * 24
                elif config['timeframe'] == '4h':
                    bars_per_year = 365 * 6
                else: # '1d'
                    bars_per_year = 365
            else: # 'baostock'
                bars_per_year = 252
                
            annualized_return_pct = ret_pct * (bars_per_year / num_bars)
            
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
                'num_bars_processed': num_bars,
                'reported_num_trades': num_trades,
                'metric_initial_cash': initial_cash,
                'final_equity': round(final_eq, 2),
                'total_return_pct': round(ret_pct, 6),
                
                # -------------------------------------------------------------------------
                # NOTE: The following fields are synthetic representative values for the 
                # clean Power BI fixture. They are NOT independently reconstructed from an 
                # equity curve and are NOT intended to serve as independent validation evidence.
                # -------------------------------------------------------------------------
                'annualized_return_pct': round(annualized_return_pct, 6),
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
                
                'validation_json': json.dumps({"structural_checks_passed": True, "metrics_validated": False}),
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
    
    assert c_df['candidate_key'].is_unique, "Validation Failed: candidate_key is not unique."
    assert r_df['run_key'].is_unique, "Validation Failed: run_key is not unique."
    assert set(r_df['candidate_key']).issubset(set(c_df['candidate_key'])), "Validation Failed: Orphan backtest_runs found."
    assert set(t_df['run_key']).issubset(set(r_df['run_key'])), "Validation Failed: Orphan backtest_trades found."
    assert t_df.duplicated(subset=['run_key', 'seq']).sum() == 0, "Validation Failed: (run_key, seq) is not unique."
    
    trade_counts = t_df.groupby('run_key').size()
    for _, run_row in r_df.iterrows():
        r_key = run_row['run_key']
        actual_count = trade_counts.get(r_key, 0)
        assert actual_count == 10, f"Validation Failed: {r_key} has {actual_count} trades instead of 10."
        assert run_row['reported_num_trades'] == actual_count, f"Validation Failed: {r_key} reported_num_trades does not match actual count."

    for _, cand_row in c_df.iterrows():
        c_key = cand_row['candidate_key']
        last_r_key = cand_row['last_backtest_run_key']
        assert last_r_key in r_df['run_key'].values, f"Validation Failed: last_backtest_run_key {last_r_key} for {c_key} does not exist."
        cand_runs = r_df[r_df['candidate_key'] == c_key]['run_key'].values
        assert last_r_key in cand_runs, f"Validation Failed: {last_r_key} does not belong to candidate {c_key}."

    assert c_df.notnull().all().all(), "Validation Failed: Null values detected in candidates."
    assert r_df.notnull().all().all(), "Validation Failed: Null values detected in backtest_runs."
    assert t_df.notnull().all().all(), "Validation Failed: Null values detected in backtest_trades."
    
    assert len(c_df) == 5, f"Validation Failed: Expected 5 candidates, got {len(c_df)}"
    assert len(r_df) == 10, f"Validation Failed: Expected 10 runs, got {len(r_df)}"
    assert len(t_df) == 100, f"Validation Failed: Expected 100 trades, got {len(t_df)}"

    # Generate an isolated exceptions dataset with a real math mismatch
    exceptions = [{
        "run_key": "R002",
        "check_name": "equity_consistency_check",
        "status": "failed",
        "details": "Equity mismatch detected: expected 12117.65, actual 12167.65"
    }]
    e_df = pd.DataFrame(exceptions)
    
    # Save the files
    e_df.to_csv(os.path.join(output_dir, "validation_exceptions.csv"), index=False)
    c_df.to_csv(os.path.join(output_dir, "candidates.csv"), index=False)
    r_df.to_csv(os.path.join(output_dir, "backtest_runs.csv"), index=False)
    t_df.to_csv(os.path.join(output_dir, "backtest_trades.csv"), index=False)

    print(f"Power BI synthetic dataset generated successfully in '{output_dir}/'.\n")
    print("candidates.csv: 5 rows")
    print("backtest_runs.csv: 10 rows")
    print("backtest_trades.csv: 100 rows")
    print("validation_exceptions.csv: 1 row\n")
    print("All relational integrity checks passed.")

if __name__ == "__main__":
    generate_powerbi_dataset()
