# Power BI DAX

The confirmed prototype measure is recorded below. Additional measures should be exported from the final PBIX/model rather than reconstructed from memory.

```DAX
Total Gross Realized P&L =
SUM(backtest_trades[realized_pnl])
```

**P&L convention:** Total Realized P&L is gross realized P&L from fills. Fees are separate and displayed as Total Fees.

