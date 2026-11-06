#!/usr/bin/env python3
"""Read-only by default: attribute legacy closes only through complete order chains."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
from uuid import UUID

import psycopg
from psycopg.rows import dict_row


async def audit(args: argparse.Namespace) -> None:
    dsn = os.environ["DATABASE_URL"].replace("postgresql+psycopg://", "postgresql://")
    async with await psycopg.AsyncConnection.connect(dsn, row_factory=dict_row) as conn:
        runs = await (
            await conn.execute(
                "SELECT * FROM strategy_runs WHERE accounting_status<>'verified' ORDER BY id"
            )
        ).fetchall()
        chains = await (
            await conn.execute("""
          SELECT c.id closed_trade_id,r.id run_id,c.account_id,c.open_order_id,c.close_order_id,
            c.open_ts,c.close_ts,c.close_profit_abs,
            (SELECT count(DISTINCT d2.run_id) FROM strategy_run_decisions d2 WHERE d2.order_id=c.close_order_id) claim_count
          FROM closed_trades c JOIN strategy_run_decisions d ON d.order_id=c.close_order_id
          JOIN strategy_runs r ON r.id=d.run_id AND r.account_id=c.account_id
          JOIN orders o ON o.client_order_id=c.open_order_id AND o.account_id=c.account_id
          JOIN orders closing ON closing.client_order_id=c.close_order_id AND closing.account_id=c.account_id
          WHERE c.run_id IS NULL AND o.venue=c.venue AND o.symbol=c.symbol
            AND closing.venue=c.venue AND closing.symbol=c.symbol
          ORDER BY c.id,r.id
        """)
        ).fetchall()
        if args.apply_metadata:
            for chain in chains:
                if chain["claim_count"] != 1:
                    continue
                await conn.execute(
                    "INSERT INTO paper_legacy_attributions(closed_trade_id,run_id,evidence) VALUES (%s,%s,%s::jsonb) ON CONFLICT(closed_trade_id) DO NOTHING",
                    (
                        chain["closed_trade_id"],
                        chain["run_id"],
                        json.dumps(chain, default=str),
                    ),
                )
            for run_id in args.confirmed_contamination:
                if not args.note:
                    raise ValueError(
                        "Confirmed contamination requires an explicit audit explanation"
                    )
                await conn.execute(
                    "UPDATE strategy_runs SET accounting_status='contaminated',accounting_note=%s WHERE id=%s AND accounting_status<>'verified'",
                    (args.note, UUID(run_id)),
                )
        report = {
            "runs": runs,
            "complete_close_chains": chains,
            "policy": "Legacy orders, cash and positions are unchanged. Symbol/time alone never proves ownership.",
        }
        Path(args.output).write_text(json.dumps(report, default=str, indent=2) + "\n")
        print(
            f"Audit saved: {len(runs)} legacy runs, {len(chains)} complete close chains"
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    parser.add_argument("--apply-metadata", action="store_true")
    parser.add_argument("--confirmed-contamination", action="append", default=[])
    parser.add_argument("--note")
    asyncio.run(audit(parser.parse_args()))
