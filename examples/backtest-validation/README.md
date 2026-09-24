# Inalpha Issue #165 — PR-ready analytical package

## Contents

- `FINDINGS.md` — final validation/methodology/findings document
- `sql/validation_checks.sql` — reproducible SQLite checks
- `RELATIONSHIP_MAP.md` — lineage and relationship model
- `DATA_DICTIONARY.md` — field semantics
- `METRIC_CATALOGUE.md` — metric definitions and reproducibility status
- `powerbi/DAX_MEASURES.md` — confirmed DAX measure
- `powerbi/POWER_QUERY.md` — Power Query packaging note

## Public-data boundary

Do not commit private source rows, the private workbook/PBIX, account data, internal UUIDs, credentials, or private archive data. The public PR should contain reproducible analytical logic, definitions, aggregate findings, synthetic fixtures where needed, and safe screenshots.

## Final repository step

Before opening the Draft PR, read the repository `CONTRIBUTING.md`, copy the exact final Power Query M and any additional final DAX measures from the working PBIX/model if required by the repository, run the checks against the approved equivalent sanitized dataset, and link the PR to Issue #165.


## Reproducing the SQLite Validation

The validation can be reproduced using the synthetic database generator included in this directory.

### 1. Generate the synthetic database

Open the repository in any code editor and navigate to:

`examples/backtest-validation/create_synthetic_db.py`

Run the Python script.

The script generates the synthetic CSV files and creates a fresh SQLite database in the same `examples/backtest-validation/` directory:

- `candidates.csv`
- `backtest_runs.csv`
- `backtest_trades.csv`
- `synthetic_validation.db`

### 2. Open the SQLite database

Open `synthetic_validation.db` using any SQLite-compatible application or SQLite command-line interface.

The database contains the three validation tables:

- `candidates`
- `backtest_runs`
- `backtest_trades`

### 3. Run the validation checks

Open the validation SQL file included in the repository:

`examples/backtest-validation/sql/validation_checks.sql`

Run the queries in this file against `synthetic_validation.db`.

The file contains the complete set of validation checks for the synthetic dataset, including relationship, uniqueness, reconciliation, required-field, denominator, data-quality, and annualized-return checks.

### 4. Compare the results

The expected results for the synthetic validation cases are documented in:

`examples/backtest-validation/EXPECTED_RESULTS.md`

The synthetic dataset intentionally contains both valid and invalid cases, so validation queries returning failure rows is expected.

The `R_VALID` record serves as the clean control case, while the other synthetic records are designed to trigger specific validation checks.

### 5. Reproduce from a fresh database

To repeat the validation from scratch, rerun:

`examples/backtest-validation/create_synthetic_db.py`

This recreates the synthetic CSV files and SQLite database, after which the same `validation_checks.sql` file can be run against the newly generated database.
