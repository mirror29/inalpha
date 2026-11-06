import importlib.util
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd


HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location(
    "fixture", HERE / "Generate data for model.py"
)
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)


class FixtureValidationTests(unittest.TestCase):
    def assert_exported_status(self, frames, expected):
        runs, exceptions = fixture.apply_validations(*frames)
        exported = pd.read_csv(io.StringIO(runs.to_csv(index=False)))
        for metadata in exported.validation_json:
            self.assertIs(json.loads(metadata)["structural_checks_passed"], expected)
            self.assertIs(json.loads(metadata)["metrics_validated"], False)
        return exceptions

    def test_required_fields_reject_nulls_in_exported_metadata(self):
        required = (
            (0, "candidate_key status author created_at_utc updated_at_utc"),
            (
                1,
                "run_key candidate_key strategy_kind status venue symbol timeframe "
                "from_ts_utc to_ts_utc initial_cash fee_rate params_json num_bars_processed "
                "reported_num_trades metric_initial_cash final_equity total_return_pct "
                "annualized_return_pct total_fees validation_json created_at_utc updated_at_utc",
            ),
            (
                2,
                "run_key seq bar_ts_utc bar_close side quantity order_type fill_price "
                "fee realized_pnl intent",
            ),
        )
        for table, columns in required:
            for column in columns.split():
                with self.subTest(table=table, column=column):
                    frames = fixture.generate_raw_data(equity_defect_amount=0)
                    frames[table].loc[0, column] = None
                    self.assert_exported_status(frames, False)

    def test_optional_candidate_fields_and_no_run_are_valid(self):
        candidates, runs, trades = fixture.generate_raw_data(equity_defect_amount=0)
        candidates.loc[0, "fitness"] = None
        candidates.loc[0, "last_backtest_run_key"] = None
        runs = runs[runs.candidate_key != "C001"].copy()
        trades = trades[trades.run_key.isin(runs.run_key)].copy()
        self.assert_exported_status((candidates, runs, trades), True)

    def test_duplicate_keys_and_orphans(self):
        for table, column, value in (
            (0, "candidate_key", "C002"),
            (1, "run_key", "R002"),
            (1, "candidate_key", "C999"),
            (2, "run_key", "R999"),
            (2, "seq", 2),
            (1, "reported_num_trades", 9),
        ):
            with self.subTest(table=table, column=column):
                frames = fixture.generate_raw_data(equity_defect_amount=0)
                frames[table].loc[0, column] = value
                self.assert_exported_status(frames, False)

    def test_latest_run_pointer_ownership_and_existence(self):
        for pointer, expected in (("R004", False), ("R999", False), ("", True)):
            with self.subTest(pointer=pointer):
                frames = fixture.generate_raw_data(equity_defect_amount=0)
                frames[0].loc[0, "last_backtest_run_key"] = pointer
                self.assert_exported_status(frames, expected)

    def test_equity_exceptions_match_scoped_sql(self):
        for amount in (0, 50, 75, 100):
            with self.subTest(amount=amount):
                frames = fixture.generate_raw_data(equity_defect_amount=amount)
                exceptions = self.assert_exported_status(frames, True)
                with sqlite3.connect(":memory:") as conn:
                    for name, frame in zip(
                        ("candidates", "backtest_runs", "backtest_trades"), frames
                    ):
                        frame.to_sql(name, conn, index=False)
                    rows = conn.execute(
                        (HERE / "equity_consistency_check.sql").read_text()
                    ).fetchall()
                self.assertEqual(len(rows), int(amount != 0))
                self.assertEqual(len(exceptions), len(rows))
                if amount:
                    self.assertEqual(rows[0][0], "R002")
                    self.assertIn(
                        f"actual {12110.87 + amount:.2f}", exceptions.iloc[0].details
                    )

    def test_documented_aggregates_and_linear_annualization(self):
        candidates, runs, trades = fixture.generate_raw_data()
        self.assertEqual((len(candidates), len(runs), len(trades)), (5, 10, 100))
        self.assertEqual(
            (len(candidates.columns), len(runs.columns), len(trades.columns)),
            (7, 38, 11),
        )
        self.assertAlmostEqual(trades.realized_pnl.sum(), 1300.50)
        self.assertAlmostEqual(
            trades[trades.run_key == "R001"].realized_pnl.sum(), 107.10
        )
        self.assertAlmostEqual(
            trades[trades.run_key.isin(["R001", "R002"])].realized_pnl.sum(), 219.30
        )
        self.assertAlmostEqual(
            runs.iloc[0].final_equity - runs.iloc[0].metric_initial_cash, 105.87
        )
        for row in runs.itertuples():
            factor = {
                "1h": 8760,
                "4h": 2190,
                "1d": 365 if row.venue == "binance" else 252,
            }[row.timeframe]
            self.assertAlmostEqual(
                row.annualized_return_pct,
                row.total_return_pct * factor / row.num_bars_processed,
                places=6,
            )

    def test_output_and_overwrite_protection(self):
        with tempfile.TemporaryDirectory() as directory:
            import os

            previous = os.getcwd()
            try:
                os.chdir(directory)
                fixture.generate_powerbi_dataset()
                output = Path("fixture_output")
                before = {path.name: path.read_bytes() for path in output.iterdir()}
                self.assertEqual(len(before), 4)
                with self.assertRaises(SystemExit):
                    fixture.generate_powerbi_dataset()
                self.assertEqual(
                    before, {path.name: path.read_bytes() for path in output.iterdir()}
                )
            finally:
                os.chdir(previous)

    def test_zero_exception_export_retains_schema(self):
        original = fixture.generate_raw_data
        with tempfile.TemporaryDirectory() as directory:
            import os

            previous = os.getcwd()
            try:
                os.chdir(directory)
                with patch.object(
                    fixture,
                    "generate_raw_data",
                    side_effect=lambda **kw: original(equity_defect_amount=0),
                ):
                    fixture.generate_powerbi_dataset()
                exceptions = pd.read_csv("fixture_output/validation_exceptions.csv")
                self.assertEqual(
                    list(exceptions), ["run_key", "check_name", "status", "details"]
                )
                self.assertTrue(exceptions.empty)
            finally:
                os.chdir(previous)


if __name__ == "__main__":
    unittest.main()
