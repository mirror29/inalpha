"""Build price-only development inputs and separate deterministic outcomes for #206."""

from __future__ import annotations

import argparse
import json
import math
import statistics
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .artifacts import digest, freeze, safe_json

PROTOCOL = {
    "version": "206-development-v0.1",
    "frozen": False,
    "asset": "TSLA",
    "benchmark": "SPY",
    "calendar": "XNYS",
    "timezone": "America/New_York",
    "horizon": 5,
    "entry_timing": "next session open; exit open five sessions after entry",
    "adjustment": "Yahoo Finance OHLCV, auto_adjust=False, actions retained separately",
    "transaction_costs": {"round_trip_bps": 20, "sensitivity_bps": [0, 50]},
    "decision_semantics": "UP/DOWN beyond frozen +/-tau, otherwise FLAT; action +1/0/-1",
    "probability_semantics": "p_up,p_flat,p_down sum to one; multiclass Brier sum",
    "neutral_treatment": "zero return; correct iff FLAT",
    "short_treatment": "negative simple excess return; borrow costs excluded",
    "thresholds": "half sample standard deviation of five-session excess returns whose exit precedes first case",
    "scope": "development machinery only; not formal evaluation or superiority evidence",
}


def write_private(path: Path, value: Any) -> None:
    """Create artifacts exclusively so a repeated command cannot overwrite provenance."""
    import os

    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as f:
        f.write(safe_json(value))


def validate_prices(rows: list[dict[str, Any]], sessions: list[str]) -> None:
    """Reject partial alignment and corrupt OHLCV before deriving features or labels."""
    if [r["session"] for r in rows] != sessions:
        raise ValueError("market series must exactly match expected exchange sessions")
    for r in rows:
        if any(not math.isfinite(r[k]) or r[k] <= 0 for k in ("open", "high", "low", "close")):
            raise ValueError("prices must be finite and positive")
        if not math.isfinite(r["volume"]) or r["volume"] < 0:
            raise ValueError("invalid volume")
        if r["low"] > min(r["open"], r["close"]) or r["high"] < max(r["open"], r["close"]):
            raise ValueError("invalid OHLC ordering")


def features(rows: list[dict[str, Any]], i: int) -> list[tuple[str, dict[str, float]]]:
    """Read only completed lookback rows; never include tickers, dates or absolute prices."""
    if i < 251:
        raise ValueError("252-session lookback required")
    closes = [r["close"] for r in rows[: i + 1]]
    daily = [closes[j] / closes[j - 1] - 1 for j in range(i - 19, i + 1)]
    volume = statistics.mean(r["volume"] for r in rows[i - 19 : i + 1])
    low = min(r["low"] for r in rows[i - 251 : i + 1])
    high = max(r["high"] for r in rows[i - 251 : i + 1])
    return [
        (
            "returns",
            {
                "return_5": closes[i] / closes[i - 5] - 1,
                "return_20": closes[i] / closes[i - 20] - 1,
            },
        ),
        ("volatility", {"realized_20": statistics.stdev(daily) * math.sqrt(252)}),
        ("volume", {"volume_ratio_20": rows[i]["volume"] / volume if volume else 0.0}),
        ("range", {"position_252": (closes[i] - low) / (high - low) if high > low else 0.5}),
    ]


def excess(
    asset: list[dict[str, Any]],
    benchmark: list[dict[str, Any]],
    i: int,
    horizon: int = 5,
    *,
    benchmark_dividends: bool = False,
) -> float:
    """Decision i enters i+1 and exits i+1+H, using matching unadjusted opens.

    With ``benchmark_dividends`` the benchmark also earns cash dividends whose ex-date
    falls in (entry, exit]: an open-to-open holder owns the share before those ex-dates.
    """
    entry, exit_ = i + 1, i + 1 + horizon
    benchmark_return = benchmark[exit_]["open"] / benchmark[entry]["open"] - 1
    if benchmark_dividends:
        cash = sum(row.get("dividends", 0.0) for row in benchmark[entry + 1 : exit_ + 1])
        benchmark_return += cash / benchmark[entry]["open"]
    return float(asset[exit_]["open"] / asset[entry]["open"] - 1 - benchmark_return)


def classify(r: float, tau: float) -> str:
    return "UP" if r > tau else "DOWN" if r < -tau else "FLAT"


def build_materials(
    market: dict[str, Any],
    case_sessions: list[str],
    *,
    benchmark_dividends: bool = False,
    tau_windows: int | None = None,
) -> dict[str, Any]:
    """Freeze evidence and deterministic labels in separate output objects.

    Defaults reproduce the 2026-10-12 development packet. ``benchmark_dividends`` and
    ``tau_windows`` (trailing calibration windows; None = all eligible) are the #206 review
    proposals and stay opt-in until the protocol freezes them.
    """
    sessions = market["sessions"]
    if (
        sessions != sorted(set(sessions))
        or not case_sessions
        or case_sessions != sorted(set(case_sessions))
    ):
        raise ValueError("sessions/cases must be nonempty, unique and sorted")
    asset, benchmark = market["asset"], market["benchmark"]
    validate_prices(asset, sessions)
    validate_prices(benchmark, sessions)
    indices = [sessions.index(s) for s in case_sessions]
    if indices[0] < 252 or indices[-1] + 6 >= len(sessions):
        raise ValueError("insufficient lookback or future outcome coverage")
    if tau_windows is not None and tau_windows < 2:
        raise ValueError("tau calibration needs at least two windows")
    calibration = [
        excess(asset, benchmark, i, benchmark_dividends=benchmark_dividends)
        for i in range(indices[0] - 6)
    ]
    if tau_windows is not None:
        if len(calibration) < tau_windows:
            raise ValueError("insufficient calibration windows for tau")
        calibration = calibration[-tau_windows:]
    tau = 0.5 * statistics.stdev(calibration)
    climatology = {
        cls: sum(classify(r, tau) == cls for r in calibration) / len(calibration)
        for cls in ("UP", "FLAT", "DOWN")
    }
    protocol = {
        **PROTOCOL,
        "tau": tau,
        "tau_calibration_windows": len(calibration),
        "benchmark_return": "price plus cash dividends with ex-date in (entry, exit]"
        if benchmark_dividends
        else "price only; benchmark dividends excluded",
        "climatology": climatology,
        "climatology_source": "label frequencies of the tau calibration windows",
        "calibration_last_exit": sessions[indices[0] - 1],
        "market_hash": digest(market),
        "development_dates": case_sessions,
        "formal_exclusion": "exclude development inputs and overlapping entry/exit windows",
    }
    cases, labels, annotations = [], [], []
    for n, i in enumerate(indices):
        case_id = f"dev-{n + 1:03d}"
        close = market["session_closes"][sessions[i]]
        evidence = []
        for identity, values in [
            *features(asset, i),
            *(("benchmark_" + key, value) for key, value in features(benchmark, i)),
        ]:
            evidence.append(
                {
                    "id": identity,
                    "kind": "price",
                    "content": values,
                    "source": "frozen-market:" + digest(market),
                    "available_at": close,
                    "session_close": close,
                }
            )
        case = freeze(case_id, close, evidence)
        cases.append(case)
        r = excess(asset, benchmark, i, benchmark_dividends=benchmark_dividends)
        value = classify(r, tau)
        labels.append(
            {
                "case_id": case_id,
                "value": value,
                "excess_return": r,
                "entry_session": sessions[i + 1],
                "exit_session": sessions[i + 6],
                "protocol_version": protocol["version"],
                "code_version": "labels-v1",
                "provenance": "frozen-market:" + digest(market),
                "tau": tau,
            }
        )
        annotations.append(
            {
                "case_id": case_id,
                "evidence_ids": [e["id"] for e in evidence],
                "criteria": [
                    "citations must refer to supplied evidence",
                    "numeric claims must match frozen values",
                    "probabilities finite and sum to one",
                    "action/argmax consistency",
                ],
                "rubric_version": "206-price-process-v1",
                "protocol_version": protocol["version"],
                "provenance": "deterministic pre-run evidence rubric",
                "blinded": True,
                "human_review": "pending; no human annotation or agreement claimed",
            }
        )
    return {
        "protocol": protocol,
        "development": cases,
        "formal": [],
        "market": {
            "sessions": sessions,
            **{
                name: [
                    {
                        "session": r["session"],
                        "source": market["source"],
                        "raw": r,
                        "hash": digest(r),
                    }
                    for r in market[name]
                ]
                for name in ("asset", "benchmark")
            },
        },
        "labels": labels,
        "annotations": annotations,
    }


def download_market(start: str, end: str) -> dict[str, Any]:
    """Acquire both series together under identical explicit vendor conventions."""
    import exchange_calendars as xcals
    import yfinance as yf

    calendar = xcals.get_calendar("XNYS")
    schedule = calendar.sessions_in_range(start, end)
    sessions = [s.strftime("%Y-%m-%d") for s in schedule]
    closes = {s.strftime("%Y-%m-%d"): calendar.session_close(s).isoformat() for s in schedule}
    result: dict[str, Any] = {
        "source": "Yahoo Finance via yfinance",
        "retrieved_at": datetime.now(UTC).isoformat(),
        "yfinance_version": yf.__version__,
        "auto_adjust": False,
        "sessions": sessions,
        "session_closes": closes,
        "availability_assumption": "daily bars become available after the recorded session close; historical vendor revisions cannot be excluded",
    }
    for name, symbol in (("asset", "TSLA"), ("benchmark", "SPY")):
        frame = yf.Ticker(symbol).history(
            start=start, end=end, interval="1d", auto_adjust=False, actions=True, raise_errors=True
        )
        result[name] = [
            {
                "session": index.strftime("%Y-%m-%d"),
                **{k: float(row[k.title()]) for k in ("open", "high", "low", "close", "volume")},
                "dividends": float(row.get("Dividends", 0)),
                "splits": float(row.get("Stock Splits", 0)),
            }
            for index, row in frame.iterrows()
            if index.strftime("%Y-%m-%d") in sessions
        ]
    # Vendor end is exclusive. Calendar must use the same end convention.
    result["sessions"] = [s for s in sessions if s < end]
    result["session_closes"] = {s: closes[s] for s in result["sessions"]}
    for name in ("asset", "benchmark"):
        validate_prices(result[name], result["sessions"])
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--market", type=Path, help="Reuse an immutable market snapshot")
    parser.add_argument("--start", default="2024-05-07")
    parser.add_argument("--end", default="2026-07-08", help="exclusive")
    parser.add_argument("--case-start", default="2026-05-01")
    parser.add_argument("--cases", type=int, default=10)
    parser.add_argument(
        "--benchmark-dividends",
        action="store_true",
        help="add benchmark cash dividends with ex-date in (entry, exit] (#206 proposal)",
    )
    parser.add_argument(
        "--tau-windows",
        type=int,
        help="estimate tau from this many trailing eligible windows (default: all)",
    )
    args = parser.parse_args()
    if not 1 <= args.cases <= 20:
        parser.error("development cases must be between 1 and 20")
    market = (
        json.loads(args.market.read_text())
        if args.market
        else download_market(args.start, args.end)
    )
    chosen = [s for s in market["sessions"] if s >= args.case_start][: args.cases]
    if len(chosen) != args.cases:
        parser.error("insufficient development sessions")
    materials = build_materials(
        market, chosen, benchmark_dividends=args.benchmark_dividends, tau_windows=args.tau_windows
    )
    args.directory.mkdir(parents=True, exist_ok=False, mode=0o700)
    write_private(args.directory / "market.json", market)
    write_private(args.directory / "materials.json", materials)
    write_private(
        args.directory / "inputs.json",
        {
            "protocol": materials["protocol"],
            "cases": materials["development"],
            "outcomes_hash": digest(materials["labels"]),
        },
    )
    write_private(args.directory / "outcomes.json", materials["labels"])
    write_private(args.directory / "annotations.json", materials["annotations"])
    print(
        json.dumps(
            {
                "directory": str(args.directory),
                "sessions": len(market["sessions"]),
                "cases": len(chosen),
                "market_hash": digest(market),
            }
        )
    )


if __name__ == "__main__":
    main()
