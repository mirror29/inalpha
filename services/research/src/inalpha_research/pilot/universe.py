"""Multi-ticker inputs and labels for #206 v0.3 §3.

Corporate actions are applied identically to stocks and the benchmark: cash dividends with
ex-date in (entry, exit] are added and splits in that window scale the exit open. Windows
with a declared non-cash action are excluded. tau and climatology come from each ticker's
own trailing calibration windows, all of which exit before the calibration cutoff.
"""

from __future__ import annotations

import statistics
from typing import Any

from .artifacts import digest, freeze
from .materials import PROTOCOL, classify, features, validate_prices

CALIBRATION_WINDOWS = 252


def holding_return(rows: list[dict[str, Any]], entry: int, exit_: int) -> float:
    """Open-to-open return of one share bought at ``entry`` with actions in (entry, exit].

    A split multiplies the shares held; a dividend pays per share held on its ex-date (a
    same-day split applies first, matching post-split per-share vendor dividends).
    """
    shares, cash = 1.0, 0.0
    for row in rows[entry + 1 : exit_ + 1]:
        if row.get("splits"):
            shares *= row["splits"]
        cash += row.get("dividends", 0.0) * shares
    return float((rows[exit_]["open"] * shares + cash) / rows[entry]["open"] - 1)


def window_excess(
    asset: list[dict[str, Any]], benchmark: list[dict[str, Any]], i: int, horizon: int = 5
) -> float:
    return holding_return(asset, i + 1, i + 1 + horizon) - holding_return(
        benchmark, i + 1, i + 1 + horizon
    )


def excluded(
    sessions: list[str], ticker: str, i: int, exclusions: list[dict[str, Any]], horizon: int = 5
) -> str | None:
    """Reason when a declared non-cash action of ``ticker`` falls in (entry, exit]."""
    inside = set(sessions[i + 2 : i + 2 + horizon])
    for action in exclusions:
        if action["ticker"] == ticker and action["session"] in inside:
            return str(action["reason"])
    return None


def screen(
    ranked: list[str],
    series: dict[str, list[dict[str, Any]]],
    sessions: list[str],
    *,
    size: int = 6,
    exclude: frozenset[str] = frozenset({"TSLA"}),
) -> dict[str, Any]:
    """Walk the pre-run ranking; a candidate with missing or invalid data is replaced by the
    next-ranked member and the reason recorded. Must run before any model call."""
    chosen: list[str] = []
    replaced: list[dict[str, str]] = []
    for ticker in ranked:
        if len(chosen) == size:
            break
        if ticker in exclude:
            replaced.append({"ticker": ticker, "reason": "EXCLUDED_BY_PROTOCOL"})
            continue
        if ticker not in series:
            replaced.append({"ticker": ticker, "reason": "NO_MARKET_FILE"})
            continue
        try:
            validate_prices(series[ticker], sessions)
        except ValueError as exc:
            replaced.append({"ticker": ticker, "reason": f"INVALID_DATA: {exc}"})
            continue
        chosen.append(ticker)
    if len(chosen) < size:
        raise ValueError("ranking exhausted before the universe was filled")
    return {"selected": chosen, "replaced": replaced, "ranking_hash": digest(ranked)}


def calibrate(
    asset: list[dict[str, Any]],
    benchmark: list[dict[str, Any]],
    sessions: list[str],
    ticker: str,
    cutoff: int,
    exclusions: list[dict[str, Any]],
    windows: int = CALIBRATION_WINDOWS,
) -> dict[str, Any]:
    """tau = half the SD of the last ``windows`` eligible excess returns exiting before
    session ``cutoff``; climatology = their label frequencies under that tau."""
    eligible = [i for i in range(cutoff - 6) if not excluded(sessions, ticker, i, exclusions)]
    if len(eligible) < windows:
        raise ValueError(f"insufficient calibration windows for {ticker}")
    values = [window_excess(asset, benchmark, i) for i in eligible[-windows:]]
    tau = 0.5 * statistics.stdev(values)
    return {
        "tau": tau,
        "windows": windows,
        "first_entry": sessions[eligible[-windows] + 1],
        "last_exit": sessions[eligible[-1] + 6],
        "climatology": {
            cls: sum(classify(r, tau) == cls for r in values) / windows
            for cls in ("UP", "FLAT", "DOWN")
        },
    }


def build_universe(
    market: dict[str, Any],
    tickers: list[str],
    decision_sessions: list[str],
    *,
    calibration_before: str | None = None,
    exclusions: list[dict[str, Any]] | None = None,
    split: str = "formal",
) -> dict[str, Any]:
    """Cases and separate labels for every ticker-date; tickers share the decision dates.

    ``market`` holds ``sessions``, ``session_closes``, ``source``, ``benchmark`` rows and
    ``tickers`` (symbol -> rows). ``calibration_before`` freezes tau for later periods
    (default: the first decision session). Case ids name ticker and date but never reach
    prompts, which receive only evidence identities and content.
    """
    sessions, exclusions = market["sessions"], exclusions or []
    if not decision_sessions or decision_sessions != sorted(set(decision_sessions)):
        raise ValueError("decision sessions must be nonempty, unique and sorted")
    benchmark = market["benchmark"]
    validate_prices(benchmark, sessions)
    indices = [sessions.index(s) for s in decision_sessions]
    cutoff = sessions.index(calibration_before or decision_sessions[0])
    if cutoff > indices[0] or indices[0] < 251 or indices[-1] + 6 >= len(sessions):
        raise ValueError("insufficient lookback, calibration or outcome coverage")
    source = "frozen-market:" + digest(market)
    calibration, cases, labels, skipped = {}, [], [], []
    for ticker in tickers:
        asset = market["tickers"][ticker]
        validate_prices(asset, sessions)
        cal = calibrate(asset, benchmark, sessions, ticker, cutoff, exclusions)
        calibration[ticker] = cal
        for i in indices:
            case_id = f"{ticker}-{sessions[i]}"
            reason = excluded(sessions, ticker, i, exclusions)
            if reason:
                skipped.append({"case_id": case_id, "reason": reason})
                continue
            close = market["session_closes"][sessions[i]]
            evidence = [
                {
                    "id": identity,
                    "kind": "price",
                    "content": values,
                    "source": source,
                    "available_at": close,
                    "session_close": close,
                }
                for identity, values in [
                    *features(asset, i),
                    *(("benchmark_" + key, value) for key, value in features(benchmark, i)),
                ]
            ]
            cases.append(freeze(case_id, close, evidence))
            r = window_excess(asset, benchmark, i)
            labels.append(
                {
                    "case_id": case_id,
                    "ticker": ticker,
                    "decision_session": sessions[i],
                    "value": classify(r, cal["tau"]),
                    "excess_return": r,
                    "entry_session": sessions[i + 1],
                    "exit_session": sessions[i + 6],
                    "protocol_version": "206-formal-v0.3",
                    "code_version": "labels-v2",
                    "provenance": source,
                    "tau": cal["tau"],
                }
            )
    protocol = {
        **PROTOCOL,
        "version": "206-formal-v0.3",
        "asset": tickers,
        "split": split,
        "benchmark_return": "price plus cash dividends with ex-date in (entry, exit]",
        "corporate_actions": "dividends added and splits scale the exit open, identically "
        "for stocks and benchmark; declared non-cash actions exclude the window",
        "calibration": calibration,
        "calibration_before": sessions[cutoff],
        "exclusions": exclusions,
        "excluded_cases": skipped,
        "decision_dates": decision_sessions,
        "market_hash": digest(market),
        "scope": "formal inputs; outcomes stay separate until the pre-run commitment",
    }
    return {"protocol": protocol, "cases": cases, "labels": labels}
