import os
import sys
import pandas as pd
from datetime import datetime, timedelta
import json


REQUIRED_COLUMNS = {
    "candidates": (
        "candidate_key",
        "status",
        "author",
        "created_at_utc",
        "updated_at_utc",
    ),
    "backtest_runs": (
        "run_key",
        "candidate_key",
        "strategy_kind",
        "status",
        "venue",
        "symbol",
        "timeframe",
        "from_ts_utc",
        "to_ts_utc",
        "initial_cash",
        "fee_rate",
        "params_json",
        "num_bars_processed",
        "reported_num_trades",
        "metric_initial_cash",
        "final_equity",
        "total_return_pct",
        "annualized_return_pct",
        "total_fees",
        "validation_json",
        "created_at_utc",
        "updated_at_utc",
    ),
    "backtest_trades": (
        "run_key",
        "seq",
        "bar_ts_utc",
        "bar_close",
        "side",
        "quantity",
        "order_type",
        "fill_price",
        "fee",
        "realized_pnl",
        "intent",
    ),
}


def generate_raw_data(
    equity_defect_run="R002",
    equity_defect_amount=50.0,
    inject_structural_defect=False,
    inject_duplicate_seq=False,
    inject_invalid_pointer=False,
    inject_null_quantity=False,
):
    """Generates the base DataFrames in memory, allowing defect injection for testing."""
    configs = [
        {
            "symbol": "BTC/USDT",
            "timeframe": "1h",
            "venue": "binance",
            "strategy_kind": "trend_following",
        },
        {
            "symbol": "ETH/USDT",
            "timeframe": "4h",
            "venue": "binance",
            "strategy_kind": "mean_reversion",
        },
        {
            "symbol": "SOL/USDT",
            "timeframe": "1d",
            "venue": "binance",
            "strategy_kind": "breakout",
        },
        {
            "symbol": "AAPL",
            "timeframe": "1d",
            "venue": "baostock",
            "strategy_kind": "stat_arb",
        },
        {
            "symbol": "MSFT",
            "timeframe": "1d",
            "venue": "baostock",
            "strategy_kind": "mean_reversion",
        },
    ]

    candidates = []
    runs = []
    trades = []
    global_run_counter = 1

    for c_idx in range(1, 6):
        candidate_key = f"C{c_idx:03d}"
        c_status = "active" if c_idx % 2 != 0 else "archived"

        candidates.append(
            {
                "candidate_key": candidate_key,
                "status": c_status,
                "author": f"algo_dev_{c_idx}",
                "fitness": 1.0 + (c_idx * 0.25),
                "last_backtest_run_key": "",
                "created_at_utc": "2023-01-01T10:00:00Z",
                "updated_at_utc": "2023-01-02T10:00:00Z",
            }
        )

        for r_idx in range(1, 3):
            run_key = f"R{global_run_counter:03d}"
            candidates[-1]["last_backtest_run_key"] = run_key

            config = configs[(global_run_counter - 1) % len(configs)]
            initial_cash = 10000.00 + (global_run_counter * 1000.00)
            num_trades = 10

            run_trades = []
            run_fees = 0.0
            run_start_date = datetime(2023, 1, 1, 0, 0, 0) + timedelta(
                days=(global_run_counter - 1) * 15
            )
            pnl_pattern = [28.0, 42.0, -18.0, 35.0, 15.0]

            for seq in range(1, num_trades + 1):
                trade_dt = run_start_date + timedelta(days=seq - 1)
                bar_ts_utc = trade_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
                is_entry = seq % 2 != 0

                bar_close = 100.0 + (global_run_counter * 10) + seq
                qty = 0.5 + (seq * 0.1)
                fee = round(qty * bar_close * 0.001, 2)

                if is_entry:
                    realized_pnl = 0.0
                else:
                    exit_idx = (seq // 2) - 1
                    realized_pnl = round(
                        pnl_pattern[exit_idx % len(pnl_pattern)]
                        * (1.0 + (global_run_counter * 0.05)),
                        2,
                    )

                run_trades.append(
                    {
                        "run_key": run_key,
                        "seq": seq,
                        "bar_ts_utc": bar_ts_utc,
                        "bar_close": bar_close,
                        "side": "buy" if is_entry else "sell",
                        "quantity": round(qty, 4),
                        "order_type": "limit" if seq % 3 == 0 else "market",
                        "fill_price": bar_close,
                        "fee": fee,
                        "realized_pnl": realized_pnl,
                        "intent": "entry" if is_entry else "exit",
                    }
                )
                run_fees += fee

            trades.extend(run_trades)
            total_pnl = sum([t["realized_pnl"] for t in run_trades])
            final_eq = initial_cash + total_pnl - run_fees

            if run_key == equity_defect_run:
                final_eq += equity_defect_amount

            ret_pct = round((final_eq - initial_cash) / initial_cash * 100, 6)
            all_realized = [
                t["realized_pnl"] for t in run_trades if t["realized_pnl"] != 0
            ]
            win_rate = (
                len([p for p in all_realized if p > 0]) / len(all_realized)
                if all_realized
                else 0.0
            )

            num_bars = 1000 + (global_run_counter * 100)
            bars_per_year = (
                365 * 24
                if config["timeframe"] == "1h"
                else (
                    365 * 6
                    if config["timeframe"] == "4h"
                    else (252 if config["venue"] != "binance" else 365)
                )
            )
            annualized_return_pct = round(ret_pct * (bars_per_year / num_bars), 6)

            runs.append(
                {
                    "run_key": run_key,
                    "candidate_key": candidate_key,
                    "strategy_kind": config["strategy_kind"],
                    "status": "completed",
                    "venue": config["venue"],
                    "symbol": config["symbol"],
                    "timeframe": config["timeframe"],
                    "from_ts_utc": run_trades[0]["bar_ts_utc"],
                    "to_ts_utc": run_trades[-1]["bar_ts_utc"],
                    "initial_cash": initial_cash,
                    "fee_rate": 0.001,
                    "params_json": json.dumps({"risk": 0.02, "sl": 0.05}),
                    "num_bars_processed": num_bars,
                    "reported_num_trades": num_trades,
                    "metric_initial_cash": initial_cash,
                    "final_equity": round(final_eq, 2),
                    "total_return_pct": ret_pct,
                    "annualized_return_pct": annualized_return_pct,
                    "annualized_volatility_pct": 15.0 + global_run_counter,
                    "max_drawdown_pct": -5.0 - (global_run_counter * 0.5),
                    "sharpe": round(1.0 + (global_run_counter * 0.1), 2),
                    "sortino": round(1.2 + (global_run_counter * 0.1), 2),
                    "calmar": round(0.8 + (global_run_counter * 0.1), 2),
                    "win_rate": round(win_rate, 4),
                    "profit_factor": round(1.5 + (global_run_counter * 0.05), 2),
                    "payoff_ratio": round(1.2 + (global_run_counter * 0.05), 2),
                    "expectancy": round(5.0 + global_run_counter, 2),
                    "exposure_pct": round(45.0 + (global_run_counter * 2.0), 2),
                    "total_fees": round(run_fees, 2),
                    "best_trade_pnl": max(all_realized) if all_realized else 0.0,
                    "worst_trade_pnl": min(all_realized) if all_realized else 0.0,
                    "max_consecutive_wins": 3,
                    "max_consecutive_losses": 1,
                    "max_drawdown_duration_bars": 20 + global_run_counter,
                    "fitness": round(1.0 + (global_run_counter * 0.2), 2),
                    "validation_json": json.dumps(
                        {"structural_checks_passed": False, "metrics_validated": False}
                    ),
                    "created_at_utc": "2023-01-01T10:00:00Z",
                    "updated_at_utc": "2023-01-02T10:00:00Z",
                }
            )
            global_run_counter += 1

    c_df = pd.DataFrame(candidates)
    r_df = pd.DataFrame(runs)
    t_df = pd.DataFrame(trades)

    if inject_structural_defect:
        broken_run = r_df.iloc[0].copy()
        broken_run["run_key"] = r_df.iloc[1]["run_key"]
        broken_run["candidate_key"] = "C999"
        r_df = pd.concat([r_df, pd.DataFrame([broken_run])], ignore_index=True)

    if inject_duplicate_seq:
        # P2 Case: Set the second R001 fill's sequence to the first fill's sequence
        t_df.at[1, "seq"] = t_df.at[0, "seq"]

    if inject_invalid_pointer:
        # P2 Case: Set C001's latest-run pointer to R004 (which is owned by C002)
        c_df.loc[c_df["candidate_key"] == "C001", "last_backtest_run_key"] = "R004"

    if inject_null_quantity:
        # P2 Case: Set the first fill's required quantity to null
        t_df.at[0, "quantity"] = None

    return c_df, r_df, t_df


def validate_data(c_df, r_df, t_df):
    """Executes dynamic structural checks and mathematical consistency validation."""
    exceptions = []
    structural_passed = True

    # 1. Structural Checks
    # Uniqueness: PKs and Composite Keys
    if c_df["candidate_key"].duplicated().any() or r_df["run_key"].duplicated().any():
        structural_passed = False
    if t_df.duplicated(subset=["run_key", "seq"]).any():
        structural_passed = False

    # Referential Integrity: Foreign Keys
    if not r_df["candidate_key"].isin(c_df["candidate_key"]).all():
        structural_passed = False
    if not t_df["run_key"].isin(r_df["run_key"]).all():
        structural_passed = False

    # Latest-run pointer validity (existence and ownership)
    for _, c_row in c_df.iterrows():
        pointer = c_row["last_backtest_run_key"]
        if pd.notna(pointer) and pointer != "":
            target_run = r_df[r_df["run_key"] == pointer]
            if (
                target_run.empty
                or target_run.iloc[0]["candidate_key"] != c_row["candidate_key"]
            ):
                structural_passed = False

    # Fill-Count verification
    trade_counts = t_df.groupby("run_key").size()
    for _, run in r_df.iterrows():
        if run["reported_num_trades"] != trade_counts.get(run["run_key"], 0):
            structural_passed = False

    for table, frame in (
        ("candidates", c_df),
        ("backtest_runs", r_df),
        ("backtest_trades", t_df),
    ):
        if frame[list(REQUIRED_COLUMNS[table])].isnull().any().any():
            structural_passed = False

    # 2. Dynamic Equity Consistency Check
    grouped = (
        t_df.groupby("run_key").agg({"realized_pnl": "sum", "fee": "sum"}).reset_index()
    )
    merged = pd.merge(r_df, grouped, on="run_key", how="left").fillna(
        {"realized_pnl": 0, "fee": 0}
    )

    for _, row in merged.iterrows():
        if pd.isna(row["initial_cash"]) or pd.isna(row["final_equity"]):
            continue
        expected = round(row["initial_cash"] + row["realized_pnl"] - row["fee"], 2)
        actual = round(row["final_equity"], 2)
        if expected != actual:
            exceptions.append(
                {
                    "run_key": row["run_key"],
                    "check_name": "equity_consistency_check",
                    "status": "failed",
                    "details": f"Equity mismatch detected: expected {expected:.2f}, actual {actual:.2f}",
                }
            )

    return exceptions, structural_passed


def apply_validations(c_df, r_df, t_df):
    exceptions, structural_passed = validate_data(c_df, r_df, t_df)

    def update_val_json(j_str):
        d = json.loads(j_str) if pd.notna(j_str) else {"metrics_validated": False}
        d["structural_checks_passed"] = structural_passed
        return json.dumps(d)

    r_df["validation_json"] = r_df["validation_json"].apply(update_val_json)
    return r_df, pd.DataFrame(exceptions)


def run_tests():
    print("Running Regression Coverage & Negative Fixtures...")

    # Test 1: Original fixture emits exact intended exception
    c1, r1, t1 = generate_raw_data(equity_defect_run="R002", equity_defect_amount=50.0)
    e1, s1 = validate_data(c1, r1, t1)
    assert s1 is True, "Structural checks should pass for standard fixture"
    assert len(e1) == 1 and e1[0]["run_key"] == "R002", (
        "Must emit exactly R002 exception"
    )
    assert "expected 12110.87, actual 12160.87" in e1[0]["details"], (
        "Exception values mismatch"
    )

    # Test 2: Removing defect produces no exception
    c2, r2, t2 = generate_raw_data(equity_defect_run="R002", equity_defect_amount=0.0)
    e2, _ = validate_data(c2, r2, t2)
    assert len(e2) == 0, "Removing defect must produce NO exceptions"

    # Test 3: Changing discrepancy updates values dynamically
    c3, r3, t3 = generate_raw_data(equity_defect_run="R002", equity_defect_amount=100.0)
    e3, _ = validate_data(c3, r3, t3)
    assert "expected 12110.87, actual 12210.87" in e3[0]["details"], (
        "Exception values must update dynamically"
    )

    # Test 4: Negative fixture (broken relationships and duplicate primary keys)
    c4, r4, t4 = generate_raw_data(inject_structural_defect=True)
    _, s4 = validate_data(c4, r4, t4)
    assert s4 is False, "Structural checks MUST fail on duplicate PKs or broken FKs"

    # Test 5: Negative fixture (duplicate composite key in trades)
    c5, r5, t5 = generate_raw_data(inject_duplicate_seq=True)
    _, s5 = validate_data(c5, r5, t5)
    assert s5 is False, "Structural checks MUST fail on duplicate (run_key, seq)"

    # Test 6: Negative fixture (invalid latest-run pointer ownership)
    c6, r6, t6 = generate_raw_data(inject_invalid_pointer=True)
    _, s6 = validate_data(c6, r6, t6)
    assert s6 is False, (
        "Structural checks MUST fail on invalid last_backtest_run_key pointer"
    )

    # Test 7: Negative fixture (null required field)
    c7, r7, t7 = generate_raw_data(inject_null_quantity=True)
    _, s7 = validate_data(c7, r7, t7)
    assert s7 is False, "Structural checks MUST fail on null required fields"

    print("All regression tests passed successfully.\n")


def generate_powerbi_dataset():
    output_dir = "fixture_output"
    if os.path.exists(output_dir):
        print(f"Error: The directory '{output_dir}' already exists.")
        sys.exit(1)

    os.makedirs(output_dir)

    c_df, r_df, t_df = generate_raw_data(
        equity_defect_run="R002", equity_defect_amount=50.0
    )
    r_df, e_df = apply_validations(c_df, r_df, t_df)

    if e_df.empty:
        e_df = pd.DataFrame(columns=["run_key", "check_name", "status", "details"])

    e_df.to_csv(os.path.join(output_dir, "validation_exceptions.csv"), index=False)
    c_df.to_csv(os.path.join(output_dir, "candidates.csv"), index=False)
    r_df.to_csv(os.path.join(output_dir, "backtest_runs.csv"), index=False)
    t_df.to_csv(os.path.join(output_dir, "backtest_trades.csv"), index=False)

    print(f"Power BI synthetic dataset generated successfully in '{output_dir}/'.")


if __name__ == "__main__":
    run_tests()
    generate_powerbi_dataset()
