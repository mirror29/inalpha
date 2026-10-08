"""Read-only real-evidence preflight; never starts models or exposes holdout outcomes."""

import argparse
import json
from datetime import UTC, datetime, timedelta
from itertools import pairwise

import psycopg
from dotenv import dotenv_values
from psycopg.rows import dict_row

SUPPORTED_EVENT_TYPES = (
    "listing",
    "delisting",
    "exploit",
    "chain_halt",
    "regulatory",
    "upgrade",
    "unlock",
    "burn",
    "partnership",
    "macro",
    "other",
)
DIRECT_EVENT_TYPES = {"listing", "delisting", "exploit", "chain_halt"}


def summarize_selection(facts: list[dict]) -> dict:
    """Count every supported hypothesis type while retaining direct-only diagnostics."""
    counts = independent_counts(facts)
    return {
        "independent_events_by_type": counts,
        "direct_independent_events_by_type": [
            row for row in counts if row["event_type"] in DIRECT_EVENT_TYPES
        ],
        "minimum_matched_event_pairs": 8,
        "coverage_sufficient": sum(row["independent_events"] for row in counts) >= 8,
    }


def independent_counts(facts: list[dict]) -> list[dict]:
    """Mirror evaluator/event_study.py's 24-hour BTC/type independence window."""
    last_seen = {}
    by_type = {}
    for fact in sorted(facts, key=lambda item: item["available_at"]):
        event_type = fact["event_type"]
        previous = last_seen.get(event_type)
        if previous is not None and fact["available_at"] - previous < timedelta(
            hours=24
        ):
            continue
        last_seen[event_type] = fact["available_at"]
        by_type[event_type] = by_type.get(event_type, 0) + 1
    return [
        {"event_type": key, "independent_events": value}
        for key, value in sorted(by_type.items())
    ]


def check_readiness(
    database_url: str, *, from_ts: datetime, as_of: datetime, timeframe: str = "4h"
) -> dict:
    """Count real BTC facts in the natural 60/20/20 split of closed bars."""
    duration = timedelta(hours={"1h": 1, "4h": 4}[timeframe])
    with psycopg.connect(
        database_url.replace("postgresql+psycopg://", "postgresql://"),
        row_factory=dict_row,
    ) as conn:
        conn.execute("SET TRANSACTION READ ONLY")
        conn.execute("SET LOCAL statement_timeout = '15s'")
        rows = conn.execute(
            """SELECT ts FROM bars WHERE venue='binance' AND symbol='BTC/USDT:USDT'
AND timeframe=%s AND ts>=%s AND ts+%s<=%s ORDER BY ts""",
            (timeframe, from_ts, duration, as_of),
        ).fetchall()
        if len(rows) < 10:
            return {
                "ready_to_attempt_search": False,
                "reason": "closed_bars_unavailable",
                "closed_bars": len(rows),
            }
        discovery_end = max(2, int(len(rows) * 0.60))
        validation_end = min(
            max(discovery_end + 2, int(len(rows) * 0.80)), len(rows) - 1
        )
        start = rows[discovery_end]["ts"] + duration
        end = rows[validation_end - 1]["ts"] + duration
        facts = conn.execute(
            """WITH latest AS (
SELECT DISTINCT ON (r.source,r.source_event_id,f.fact_key)
 f.event_type,f.available_at,f.asset_ids,f.retracted,f.severity,f.confidence,
 r.source,r.source_event_id,r.collector_version,r.policy_version
FROM market_event_facts f JOIN raw_market_events r ON r.event_id=f.raw_event_id
WHERE f.available_at<=%s AND r.accepted_at<=%s
ORDER BY r.source,r.source_event_id,f.fact_key,f.available_at DESC,f.version DESC,f.fact_id
)
SELECT event_type,available_at
FROM latest WHERE NOT retracted AND 'asset:BTC'=ANY(asset_ids)
AND source IN ('coindesk','kraken_blog')
AND collector_version='selected-news-forward@1' AND policy_version='first-seen-only-v1'
AND event_type=ANY(%s)
AND severity>=0.5 AND confidence>=0.6 AND available_at>=%s AND available_at<=%s
ORDER BY available_at,source,source_event_id""",
            (as_of, as_of, list(SUPPORTED_EVENT_TYPES), start, end),
        ).fetchall()
        selection = summarize_selection(facts)
        fresh = as_of - (rows[-1]["ts"] + duration) < duration * 2
        contiguous = all(b["ts"] - a["ts"] == duration for a, b in pairwise(rows))
        return {
            "checked_at": as_of.isoformat(),
            "from_ts": from_ts.isoformat(),
            "closed_bars": len(rows),
            "timeframe": timeframe,
            "bars_fresh": fresh,
            "bars_contiguous": contiguous,
            "selection_start": start.isoformat(),
            "selection_end": end.isoformat(),
            **selection,
            "ready_to_attempt_search": selection["coverage_sufficient"]
            and fresh
            and contiguous,
            "qualification": "Necessary input check only; matched controls, FDR, profitability and Forward remain unverified.",
        }


def main() -> None:
    """Load only the database URL from the supplied environment file and print counts."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", required=True)
    parser.add_argument("--timeframe", choices=("1h", "4h"), default="4h")
    parser.add_argument("--from-ts", default="2026-09-04T00:00:00Z")
    args = parser.parse_args()
    start = datetime.fromisoformat(args.from_ts.replace("Z", "+00:00"))
    if start.tzinfo is None:
        parser.error("from-ts requires timezone")
    url = dotenv_values(args.env_file).get("DATABASE_URL")
    if not url:
        parser.error("DATABASE_URL is unavailable")
    try:
        report = check_readiness(
            url, from_ts=start, as_of=datetime.now(UTC), timeframe=args.timeframe
        )
    except psycopg.Error:
        parser.exit(2, "Readiness query failed; check local database availability.\n")
    print(json.dumps(report, indent=2, default=str))


if __name__ == "__main__":
    main()
