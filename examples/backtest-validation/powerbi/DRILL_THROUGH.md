# Power BI Drill-Through and Analytical Exploration

This document describes the analytical and drill-through workflow used in the
Power BI prototype.

Power BI is used here as an **analytical exploration layer**. Validation of
data quality and metric consistency is performed separately through the SQL
validation layer.

---

## Analytical Entry Point

The main report page is the **Lineage dashboard main** page.

The dashboard begins with realized P&L as the primary analytical measure and
allows the result to be explored through progressively more specific
dimensions.

The visible decomposition path is:

```text
Total Realized P&L
        │
        ▼
   candidate_key
        │
        ▼
      run_key
        │
        ▼
      symbol
        │
        ▼
    timeframe
```

This allows the user to move from aggregate realized P&L toward the specific
candidate, run, instrument, and timeframe associated with the result.

---

## Main Dashboard Filtering

The main dashboard provides filtering/context through fields including:

- `candidate_key`
- `run_key`
- `symbol`
- `timeframe`

These fields provide a way to narrow the analytical context before inspecting
a specific result.

The report therefore supports analysis at multiple levels without requiring
the underlying tables to be flattened.

---

## Decomposition Analysis

The primary decomposition visual begins with:

**Sum of `realized_pnl`**

The result can then be broken down by:

1. `candidate_key`
2. `run_key`
3. `symbol`
4. `timeframe`

This makes it possible to identify where realized P&L is coming from within the
candidate/run hierarchy.

For example:

```text
Total realized P&L
        │
        ├── candidate_031
        │       │
        │       └── run_028
        │               │
        │               └── ETH/USDT
        │                       │
        │                       └── 1h
        │
        ├── candidate_030
        │
        └── ...
```

The decomposition is intended for exploration rather than validation.

---

## Contextual Information

The main dashboard also exposes contextual information when inspecting a
selected result.

The visible analytical context includes fields such as:

- timeframe;
- realized P&L;
- first side;
- total quantity;
- first intent;
- number of trades;
- total fees;
- Calmar;
- payoff ratio;
- fitness.

These values provide additional context around the selected candidate/run
without requiring the user to immediately inspect individual fills.

---

## Drill-Through Workflow

The report provides a drill-through path from the main lineage analysis to a
dedicated **Basic Details** page.

The main dashboard explicitly prompts the user:

> Right-click to drill through

The intended workflow is:

```text
Main lineage analysis
        │
        ▼
Select candidate / run
        │
        ▼
Right-click
        │
        ▼
Drill through
        │
        ▼
Basic Details
```

The drill-through preserves the selected analytical context so that the
details page can focus on the selected run.

---

# Basic Details

The **Basic Details** page provides a more detailed view of the selected
backtest run.

The page identifies the selected candidate and run at the top.

The visible context includes:

- candidate number;
- run key.

For example, the screenshot shows a selected candidate and corresponding
backtest run before displaying the detailed metrics below.

---

## Run-Level Summary

The Basic Details page provides summary cards for the selected run.

The visible cards are:

### Total Realized P&L

Displays the total realized P&L associated with the selected run.

### Total Fee

Displays the total fees associated with the selected run.

### Total Fills

Displays the number of fill/trade records associated with the selected run.

### Win Rate

Displays the run-level win-rate value.

### Fitness

Displays the run-level fitness value.

These values provide a compact summary of the selected backtest before
examining the underlying fill sequence.

---

## Fill-Level Analysis

The main time/sequence visual plots information across the run's fill
sequence.

The visible series are:

- **Realized PnL**
- **Fee**
- **Price of share**

The horizontal axis is:

**Run Seq**

This allows the user to inspect how realized P&L, fees, and price information
change across the sequence of fills in the selected run.

The visual is therefore useful for investigating the shape and sequence of
the trading activity rather than only looking at the final aggregate result.

---

## Realized P&L, Fee and Price Summary

The Basic Details page also contains a donut visualization titled:

**Realized PnL, Fee and Price of share**

This provides a visual summary of the values displayed for the selected run.

It complements the sequential chart by providing an aggregate visual view
alongside the fill-by-fill sequence.

---

## Analytical Questions Supported

The drill-through page is designed to answer questions such as:

- Which candidate and run are currently being investigated?
- What was the selected run's total realized P&L?
- How much total fee was associated with the run?
- How many fills occurred?
- What was the reported win rate?
- What was the reported fitness?
- How did realized P&L change across the fill sequence?
- How did fees and price information vary across the sequence?
- What does the selected run look like when examined below the aggregate
  candidate/run level?

---

## Relationship to SQL Validation

The Basic Details page should **not** be interpreted as a second validation
engine.

The responsibilities are intentionally separated:

| Layer | Responsibility |
|---|---|
| **SQLite / SQL validation** | Data-quality, relationship, consistency, and annualization validation |
| **Power BI** | Analytical exploration, filtering, aggregation, visualization, and drill-through |

For example, Power BI can display the realized P&L and fees associated with a
run, while the SQL validation queries determine whether reported values and
derived values are internally consistent.

This separation prevents the analytical dashboard from being presented as an
independent validation implementation.

---

## Scope

The drill-through workflow is intentionally lightweight.

It uses Power BI's native filtering, aggregation, decomposition, and
drill-through capabilities rather than introducing a second set of custom
validation rules.

The purpose is to make validated/loaded backtest data easier to explore and
trace from:

```text
Candidate
    ↓
Run
    ↓
Symbol / Timeframe
    ↓
Run-level metrics
    ↓
Fill sequence
```

The Power BI report therefore complements the SQL validation layer rather than
duplicating it.

Realized P&L is used as the primary analytical value because it is recorded at the trade/fill level in backtest_trades. Summing it allows the decomposition tree to trace the realized trading result through candidate, run, symbol, and timeframe before drilling into the underlying fill sequence. Other run-level performance metrics remain available as contextual fields rather than being independently reconstructed in DAX.
