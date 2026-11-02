"""Multi-ticker labels and formal inference (#206 v0.3 §3)."""

import math
from datetime import UTC, datetime, timedelta

import pytest

from inalpha_research.pilot.inference import (
    case_scores,
    claims,
    date_bootstrap,
    formal_inference,
    holm,
    skill,
)
from inalpha_research.pilot.universe import (
    build_universe,
    holding_return,
    screen,
    window_excess,
)

SESSIONS = [
    (datetime(2024, 1, 1, tzinfo=UTC) + timedelta(days=i)).date().isoformat() for i in range(320)
]


def series(wiggle, phase=0.0):
    rows = []
    for i, s in enumerate(SESSIONS):
        level = 100 * (1 + wiggle * math.sin(0.7 * i + phase))
        rows.append(
            {
                "session": s,
                "open": level,
                "close": level * 1.001,
                "low": level * 0.95,
                "high": level * 1.05,
                "volume": 1000 + i,
                "dividends": 0.0,
                "splits": 0.0,
            }
        )
    return rows


def market(**tickers):
    return {
        "sessions": SESSIONS,
        "session_closes": {s: s + "T21:00:00Z" for s in SESSIONS},
        "source": "synthetic only",
        "benchmark": series(0.002),
        "tickers": tickers or {"AAA": series(0.01), "BBB": series(0.03, 1.0)},
    }


def test_holding_return_applies_splits_and_post_split_dividends():
    rows = [{"open": 100.0}, {"open": 100.0}, {"open": 50.0, "splits": 2.0}, {"open": 50.0}]
    assert holding_return(rows, 0, 3) == pytest.approx(0.0)
    rows[3]["dividends"] = 1.0  # per post-split share, two shares held
    assert holding_return(rows, 0, 3) == pytest.approx(0.02)
    rows[2]["dividends"] = 0.5  # same-day split applies first
    assert holding_return(rows, 0, 3) == pytest.approx(0.03)
    # An action on the entry day is already in the entry open.
    rows[0]["splits"] = 4.0
    assert holding_return(rows, 0, 3) == pytest.approx(0.03)


def test_benchmark_gets_identical_action_treatment():
    asset, benchmark = series(0.01), series(0.002)
    base = window_excess(asset, benchmark, 260)
    benchmark[263]["dividends"] = 1.0
    assert window_excess(asset, benchmark, 260) == pytest.approx(
        base - 1.0 / benchmark[261]["open"]
    )


def test_split_in_window_does_not_move_the_label():
    plain = build_universe(market(AAA=series(0.01)), ["AAA"], SESSIONS[262:263])
    split = series(0.01)
    for row in split[265:]:
        for key in ("open", "close", "low", "high"):
            row[key] /= 10
    split[265]["splits"] = 10.0
    adjusted = build_universe(market(AAA=split), ["AAA"], SESSIONS[262:263])
    assert adjusted["labels"][0]["excess_return"] == pytest.approx(
        plain["labels"][0]["excess_return"]
    )
    assert adjusted["labels"][0]["value"] == plain["labels"][0]["value"]


def test_screen_replaces_bad_candidates_with_reasons():
    good = series(0.01)
    broken = series(0.01)[:-1]
    data = {"TSLA": good, "AAA": good, "BAD": broken, "CCC": good, "DDD": good}
    result = screen(["TSLA", "AAA", "BAD", "GONE", "CCC", "DDD"], data, SESSIONS, size=3)
    assert result["selected"] == ["AAA", "CCC", "DDD"]
    assert [r["reason"].split(":")[0] for r in result["replaced"]] == [
        "EXCLUDED_BY_PROTOCOL",
        "INVALID_DATA",
        "NO_MARKET_FILE",
    ]
    with pytest.raises(ValueError, match="exhausted"):
        screen(["AAA"], data, SESSIONS, size=2)


def test_per_ticker_tau_climatology_exclusions_and_frozen_calibration():
    source = market()
    action = {"ticker": "BBB", "session": SESSIONS[285], "reason": "SPIN_OFF"}
    dates = SESSIONS[270:300:5]
    built = build_universe(source, ["AAA", "BBB"], dates, exclusions=[action])
    cal = built["protocol"]["calibration"]
    assert cal["AAA"]["tau"] != cal["BBB"]["tau"]
    assert all(c["windows"] == 252 for c in cal.values())
    assert all(sum(c["climatology"].values()) == pytest.approx(1) for c in cal.values())
    assert built["protocol"]["excluded_cases"] == [
        {"case_id": f"BBB-{SESSIONS[280]}", "reason": "SPIN_OFF"}
    ]
    assert len(built["labels"]) == len(built["cases"]) == 2 * len(dates) - 1
    assert {label["tau"] for label in built["labels"] if label["ticker"] == "AAA"} == {
        cal["AAA"]["tau"]
    }
    # Later period reuses tau frozen before the first primary decision.
    later = build_universe(source, ["AAA"], SESSIONS[300:305], calibration_before=dates[0])
    assert later["protocol"]["calibration"]["AAA"] == cal["AAA"]
    # An excluded window inside the calibration span is dropped, not counted.
    early = {"ticker": "AAA", "session": SESSIONS[100], "reason": "MERGER"}
    shifted = build_universe(source, ["AAA"], dates, exclusions=[early])
    assert shifted["protocol"]["calibration"]["AAA"]["first_entry"] < cal["AAA"]["first_entry"]
    assert "AAA" not in str(built["cases"][0]["evidence"])


def run(case, arm, p_up, status="completed"):
    return {
        "case_id": case,
        "arm": arm,
        "status": status,
        "decision": {"p_up": p_up, "p_flat": 1 - p_up, "p_down": 0.0, "action": 1},
    }


def label(case, ticker, date, value="UP"):
    return {
        "case_id": case,
        "ticker": ticker,
        "decision_session": date,
        "value": value,
        "excess_return": 0.01,
    }


CLIM = {"AAA": {"UP": 0.3, "FLAT": 0.4, "DOWN": 0.3}, "BBB": {"UP": 0.5, "FLAT": 0.2, "DOWN": 0.3}}


def panel(dates=12, d_up=0.8):
    labels, runs = [], []
    for n in range(dates):
        for ticker in CLIM:
            case = f"{ticker}-{n:02d}"
            labels.append(label(case, ticker, f"2025-01-{n + 1:02d}"))
            runs += [run(case, arm, 0.5) for arm in "ABC"] + [run(case, "D", d_up)]
    return runs, labels


def test_pooled_skill_uses_each_ticker_climatology_and_strict_itt():
    runs, labels = panel(dates=2)
    runs[0]["status"] = "invalid"
    scores = case_scores(runs, labels, CLIM)
    assert scores[0]["brier"]["A"] == 2.0
    assert scores[0]["climatology_brier"] == pytest.approx(0.7**2 + 0.4**2 + 0.3**2)
    assert scores[1]["climatology_brier"] == pytest.approx(0.5**2 + 0.2**2 + 0.3**2)
    pooled = skill(scores, "D")
    total = sum(r["climatology_brier"] for r in scores)
    assert pooled == pytest.approx(1 - sum(r["brier"]["D"] for r in scores) / total)
    with pytest.raises(ValueError, match="every arm"):
        case_scores(runs[1:], labels, CLIM)


def test_holm_step_down():
    tests = holm({"D-A": 0.01, "D-B": 0.04, "D-C": 0.03})
    assert tests["D-A"]["p_holm"] == pytest.approx(0.03)
    assert tests["D-C"]["p_holm"] == pytest.approx(0.06)
    assert tests["D-B"]["p_holm"] == pytest.approx(0.06)
    assert [tests[k]["reject"] for k in ("D-A", "D-B", "D-C")] == [True, False, False]


def test_date_bootstrap_moves_tickers_together_and_is_seeded():
    runs, labels = panel()
    # Uneven per-date differences keep the bootstrap distribution non-degenerate.
    for r in runs:
        if r["arm"] == "D" and r["case_id"].startswith("BBB") and int(r["case_id"][-2:]) % 2:
            r["decision"]["p_up"] = 0.55
    scores = case_scores(runs, labels, CLIM)
    first = date_bootstrap(scores, resamples=400)
    assert first == date_bootstrap(scores, resamples=400)
    assert first["dates"] == 12 and first["cases"] == 24
    contrast = first["contrasts"]["D-A"]
    assert contrast["difference"] < 0 and contrast["interval_95"][1] < 0
    assert contrast["p"] < 0.05 and contrast["reject"]
    assert date_bootstrap(scores[:4], block=4)["reason"] == "TOO_FEW_DATES"


def test_formal_inference_reports_sensitivity_and_claims_need_forward_agreement():
    runs, labels = panel()
    result = formal_inference(runs, labels, CLIM, resamples=200)
    assert [s["block_length"] for s in result["sensitivity"]] == [2, 8]
    assert result["primary"]["block_length"] == 4
    assert set(result["per_ticker_bss"]) == {"AAA", "BBB"}
    worse_runs, worse_labels = panel(d_up=0.2)
    forward = formal_inference(worse_runs, worse_labels, CLIM, resamples=200)
    verdict = claims(result, forward)
    assert verdict["D-A"]["retrospective_reject"]
    assert not verdict["D-A"]["same_direction_forward"] and not verdict["D-A"]["claim"]
    assert claims(result, result)["D-A"]["claim"]


def test_bootstrap_needs_an_alternative_block():
    # Four dates, block 4: the only block is the whole sequence, so no interval exists.
    runs, labels = panel(dates=4)
    for r, d in zip(
        [r for r in runs if r["arm"] == "D" and r["case_id"].startswith("AAA")],
        [-1, 1, -1, 0.9],
        strict=True,
    ):
        r["decision"]["p_up"] = 0.5 - d / 2
    scores = case_scores(runs, labels, CLIM)
    assert date_bootstrap(scores, block=4, resamples=200)["reason"] == "TOO_FEW_DATES"
    assert date_bootstrap(scores, block=2, resamples=200)["contrasts"] is not None
    runs, labels = panel(dates=8)
    result = formal_inference(runs, labels, CLIM, resamples=200)
    assert result["primary"]["contrasts"] is not None
    assert [s.get("reason") for s in result["sensitivity"]] == [None, "TOO_FEW_DATES"]


def contrast(difference, reject=True):
    return {"primary": {"contrasts": {"D-A": {"difference": difference, "reject": reject}}}}


def test_zero_difference_has_no_direction_in_either_period():
    assert claims(contrast(0.3), contrast(0.1))["D-A"]["claim"]
    assert claims(contrast(-0.3), contrast(-0.1))["D-A"]["favours"] == "D"
    for retro, forward in [(0.3, 0.0), (-0.3, 0.0), (0.0, 0.2), (0.0, -0.2), (0.0, 0.0)]:
        verdict = claims(contrast(retro), contrast(forward))["D-A"]
        assert not verdict["same_direction_forward"] and not verdict["claim"]
    assert claims(contrast(0.0), contrast(0.0))["D-A"]["favours"] is None
