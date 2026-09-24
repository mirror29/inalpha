import csv
import sqlite3
from pathlib import Path

# ---------------------------------------------------------
# Location
# ---------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent

db_path = BASE_DIR / "synthetic_validation.db"

candidates_csv = BASE_DIR / "candidates.csv"
backtest_runs_csv = BASE_DIR / "backtest_runs.csv"
backtest_trades_csv = BASE_DIR / "backtest_trades.csv"


# ---------------------------------------------------------
# Synthetic data
# ---------------------------------------------------------

candidates_data = """candidate_key,fitness
C_VALID,1.50
C_DUP,1.00
C_DUP,1.00
C_ORPHAN,2.00
C_BAD_FIT,-5.00
"""

backtest_runs_data = """run_key,candidate_key,reported_num_trades,total_fees,initial_cash,metric_initial_cash,final_equity,total_return_pct,from_ts_utc,to_ts_utc,fee_rate,timeframe,num_bars_processed,annualized_return_pct
R_VALID,C_VALID,2,2.00,10000.50,10000.50,11000.55,10.0,2023-01-01,2023-12-31,0.001,1d,365,10.0
R_DUP_RUN,C_VALID,1,1.0,1000.0,1000.0,1000.0,0.0,2023-01-01,2023-12-31,0.0,1d,10,0.0
R_DUP_RUN,C_VALID,1,1.0,1000.0,1000.0,1000.0,0.0,2023-01-01,2023-12-31,0.0,1d,10,0.0
R_ZERO_FILL,C_VALID,0,0.0,1000.0,1000.0,1000.0,0.0,2023-01-01,2023-12-31,0.0,1d,10,0.0
R_DUP_TRADE,C_VALID,2,2.00,1000.0,1000.0,1000.0,0.0,2023-01-01,2023-12-31,0.0,1d,10,0.0
R_ORPHAN,C_MISSING,1,1.0,1000.0,1000.0,1000.0,0.0,2023-01-01,2023-12-31,0.0,1d,10,0.0
R_BAD_MATH,C_VALID,5,10.0,10000.0,5000.0,20000.0,99.0,2024-01-01,2023-01-01,-0.05,7d,-10,0.0
R_BAD_TF,C_VALID,1,1.0,1000.0,1000.0,1000.0,0.0,2023-01-01,2023-12-31,0.0,2d,10,0.0
R_NULLS,C_VALID,,,,,,,,,,,,
R_ZERO_CASH,C_VALID,1,1.0,0.0,0.0,1000.0,0.0,2023-01-01,2023-12-31,0.0,1d,10,0.0
R_ANN_ERR,C_VALID,1,1.0,10000.0,10000.0,11000.0,10.0,2023-01-01,2023-12-31,0.0,1d,365,99.9
"""

backtest_trades_data = """run_key,seq,quantity,fill_price,fee
R_VALID,1,5.0,100.0,1.0
R_VALID,2,5.0,110.0,1.0
R_DUP_RUN,1,1.0,100.0,1.0
R_DUP_TRADE,1,1.0,1.0,1.0
R_DUP_TRADE,1,1.0,1.0,1.0
R_ORPHAN,1,1.0,100.0,1.0
R_BAD_MATH,1,-5.0,-10.0,2.0
R_BAD_TF,1,1.0,100.0,1.0
R_ZERO_CASH,1,1.0,100.0,1.0
R_ANN_ERR,1,1.0,100.0,1.0
R_MISSING_RUN,1,1.0,1.0,0.0
"""


# ---------------------------------------------------------
# 1. Generate CSV files
# ---------------------------------------------------------

candidates_csv.write_text(candidates_data, encoding="utf-8")
backtest_runs_csv.write_text(backtest_runs_data, encoding="utf-8")
backtest_trades_csv.write_text(backtest_trades_data, encoding="utf-8")


# ---------------------------------------------------------
# 2. Create a fresh SQLite database
# ---------------------------------------------------------

if db_path.exists():
    db_path.unlink()

conn = sqlite3.connect(db_path)
cursor = conn.cursor()


# ---------------------------------------------------------
# 3. Create tables with correct data types
# ---------------------------------------------------------

cursor.execute("""
CREATE TABLE candidates (
    candidate_key TEXT,
    fitness REAL
);
""")

cursor.execute("""
CREATE TABLE backtest_runs (
    run_key TEXT,
    candidate_key TEXT,
    reported_num_trades INTEGER,
    total_fees REAL,
    initial_cash REAL,
    metric_initial_cash REAL,
    final_equity REAL,
    total_return_pct REAL,
    from_ts_utc TEXT,
    to_ts_utc TEXT,
    fee_rate REAL,
    timeframe TEXT,
    num_bars_processed INTEGER,
    annualized_return_pct REAL
);
""")

cursor.execute("""
CREATE TABLE backtest_trades (
    run_key TEXT,
    seq INTEGER,
    quantity REAL,
    fill_price REAL,
    fee REAL
);
""")


# ---------------------------------------------------------
# 4. Import CSV data into SQLite
# ---------------------------------------------------------

with open(candidates_csv, "r", encoding="utf-8", newline="") as f:
    reader = csv.DictReader(f)

    cursor.executemany(
        """
        INSERT INTO candidates
        (candidate_key, fitness)
        VALUES (?, ?)
        """,
        [
            (
                row["candidate_key"],
                float(row["fitness"])
            )
            for row in reader
        ]
    )


with open(backtest_runs_csv, "r", encoding="utf-8", newline="") as f:
    reader = csv.DictReader(f)

    rows = []

    for row in reader:
        rows.append((
            row["run_key"],
            row["candidate_key"],
            int(row["reported_num_trades"]
                ) if row["reported_num_trades"] else None,
            float(row["total_fees"]) if row["total_fees"] else None,
            float(row["initial_cash"]) if row["initial_cash"] else None,
            float(row["metric_initial_cash"]
                  ) if row["metric_initial_cash"] else None,
            float(row["final_equity"]) if row["final_equity"] else None,
            float(row["total_return_pct"]
                  ) if row["total_return_pct"] else None,
            row["from_ts_utc"] or None,
            row["to_ts_utc"] or None,
            float(row["fee_rate"]) if row["fee_rate"] else None,
            row["timeframe"] or None,
            int(row["num_bars_processed"]
                ) if row["num_bars_processed"] else None,
            float(row["annualized_return_pct"]
                  ) if row["annualized_return_pct"] else None
        ))

    cursor.executemany(
        """
        INSERT INTO backtest_runs
        (
            run_key,
            candidate_key,
            reported_num_trades,
            total_fees,
            initial_cash,
            metric_initial_cash,
            final_equity,
            total_return_pct,
            from_ts_utc,
            to_ts_utc,
            fee_rate,
            timeframe,
            num_bars_processed,
            annualized_return_pct
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows
    )


with open(backtest_trades_csv, "r", encoding="utf-8", newline="") as f:
    reader = csv.DictReader(f)

    rows = []

    for row in reader:
        rows.append((
            row["run_key"],
            int(row["seq"]),
            float(row["quantity"]),
            float(row["fill_price"]),
            float(row["fee"])
        ))

    cursor.executemany(
        """
        INSERT INTO backtest_trades
        (
            run_key,
            seq,
            quantity,
            fill_price,
            fee
        )
        VALUES (?, ?, ?, ?, ?)
        """,
        rows
    )


# ---------------------------------------------------------
# 5. Save database
# ---------------------------------------------------------

conn.commit()
conn.close()

print("Synthetic database created successfully.")
print(f"Database: {db_path}")
