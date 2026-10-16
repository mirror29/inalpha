"""Development runner boundaries: paired failures, frozen evidence, retries and spend."""

import copy
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace as NS

import pytest

from inalpha_research.pilot.artifacts import PilotArtifacts, validate_materials
from inalpha_research.pilot.campaign import campaign
from inalpha_research.pilot.experiment import (
    AttemptRunner,
    BudgetStop,
    CampaignBudget,
    recorder_for,
    validate_decision,
)
from inalpha_research.pilot.materials import build_materials, excess
from inalpha_research.pilot.report import (
    NEUTRAL_CORPUS,
    build_report,
    intervention_plans,
    sample_mean_run,
)

CONFIG = {
    "provider": "deepseek",
    "model": "deepseek-flash",
    "limit_usd": "5",
    "baseline_tokens": 4000,
    "arm_tokens": 24000,
    "units": 2,
    "rounds": 2,
    "pricing": {
        "currency": "USD",
        "version": "fixture",
        "input_usd_per_million": "0.3",
        "output_usd_per_million": "1.2",
    },
}


class Store:
    def __init__(self):
        self.starts = []
        self.ends = []

    async def begin(self, *args):
        self.starts.append(args)

    async def settle(self, *args):
        self.ends.append(args)


def market():
    dates = [
        (datetime(2024, 1, 1, tzinfo=UTC) + timedelta(days=i)).date().isoformat()
        for i in range(300)
    ]
    rows = [
        {
            "session": s,
            "open": 100 + i / 10,
            "close": 100.5 + i / 10,
            "low": 99 + i / 10,
            "high": 102 + i / 10,
            "volume": 1000 + i,
        }
        for i, s in enumerate(dates)
    ]
    return {
        "sessions": dates,
        "asset": rows,
        "benchmark": copy.deepcopy(rows),
        "source": "synthetic only",
        "session_closes": {s: s + "T20:00:00Z" for s in dates},
    }


def materials():
    source = market()
    return build_materials(source, source["sessions"][260:270])


def decision(ids=None):
    return {
        "p_up": 0.6,
        "p_flat": 0.3,
        "p_down": 0.1,
        "action": 1,
        "cited_evidence_ids": ["returns"] if ids is None else ids,
        "rationale": "fixture assessment",
    }


def response(value=None, usage=True, finish="stop"):
    return NS(
        choices=[NS(finish_reason=finish, message=NS(content=json.dumps(value or decision())))],
        model="fixture",
        usage=NS(prompt_tokens=100, completion_tokens=100) if usage else None,
    )


def test_materials_alignment_and_future_isolation():
    source = market()
    cases = source["sessions"][260:270]
    before = build_materials(source, cases)
    changed = copy.deepcopy(source)
    changed["asset"][280]["open"] *= 1.01
    after = build_materials(changed, cases)
    assert before["protocol"]["tau"] == after["protocol"]["tau"]
    assert [e["content"] for e in before["development"][0]["evidence"]] == [
        e["content"] for e in after["development"][0]["evidence"]
    ]
    assert not validate_materials(before)
    source["benchmark"].pop()
    with pytest.raises(ValueError, match="sessions"):
        build_materials(source, cases)


@pytest.mark.parametrize(
    "patch",
    [
        {"p_up": float("nan")},
        {"p_up": -0.1},
        {"p_flat": 0.9},
        {"action": True},
        {"cited_evidence_ids": ["future"]},
    ],
)
def test_invalid_outputs(patch):
    with pytest.raises(ValueError):
        validate_decision({**decision(), **patch}, {"returns"})


async def test_complete_four_arm_campaign_and_replays(tmp_path):
    data = materials()
    captured = []
    store = Store()

    async def invoke(**kwargs):
        payload = json.loads(kwargs["user"])
        captured.append(payload)
        return response(decision([payload["evidence"][0]["id"]]))

    result = await campaign(
        {"protocol": data["protocol"], "cases": data["development"][:1]},
        tmp_path / "campaign",
        CONFIG,
        store,
        "test:owner",
        invoke,
    )
    assert len(result["runs"]) == 4
    assert all(r["status"] == "completed" and not r["artifact_validation"] for r in result["runs"])
    d = result["runs"][-1]
    assert all(
        r["replay"]["base_run_id"] == d["run_id"] for r in result["replays"] if "skip" not in r
    )
    assert d["completed_units"] == 6
    assert len(store.starts) == len(store.ends)
    assert all(
        "TSLA" not in json.dumps(p)
        and "2024-" not in json.dumps(p)
        and '"excess_return":' not in json.dumps(p)
        for p in captured
    )
    report = build_report(result["runs"], data["labels"][:1], result["replays"])
    assert report["complete_case_count"] == 1
    for replay in result["replays"]:
        if replay.get("replay", {}).get("name") == "round_zero_noise":
            replay["decision"]["action"] = -d["decision"]["action"]
    adjusted = build_report(result["runs"], data["labels"][:1], result["replays"])
    rows = {row["name"]: row for row in adjusted["interventions"]}
    assert rows["round_zero_noise"]["flip"] == 1
    assert rows["round_zero_noise"]["noise_adjusted_flip"] is None
    assert rows["downstream_noise"]["noise_adjusted_flip"] is None
    assert rows["remove_evidence"]["noise_adjusted_flip"] == -1
    assert rows["delete_contribution"]["noise_adjusted_flip"] == 0
    assert {r["name"] for r in report["interventions"]} >= {
        "flip_conclusion",
        "delete_contribution",
        "remove_evidence",
        "downstream_noise",
    }
    plans = intervention_plans(d, data["development"][0])
    assert next(p for p in plans if p["name"] == "delete_contribution")["prefix"][-1]["omitted"]
    # Omitted turns are never model-visible and no payload carries removal or meta markers.
    assert all(
        all(not h.get("omitted") for h in p.get("history", []))
        and '"removed"' not in json.dumps(p)
        and "Intervention" not in json.dumps(p)
        and "unrelated" not in json.dumps(p)
        for p in captured
    )
    assert d["history"][3]["output"] == decision() | {"action_probability_violation": False}


async def test_unknown_usage_stops_entire_campaign_and_keeps_all_cases(tmp_path):
    data = materials()
    calls = 0

    async def invoke(**kwargs):
        nonlocal calls
        calls += 1
        return response(usage=False)

    result = await campaign(
        {"protocol": data["protocol"], "cases": data["development"][:2]},
        tmp_path / "unknown",
        CONFIG,
        Store(),
        "test:owner",
        invoke,
    )
    assert calls == 1
    assert len(result["runs"]) == 8
    assert all(r["status"] == "invalid" for r in result["runs"])
    report = build_report(result["runs"], data["labels"][:2], result["replays"])
    assert report["arms"]["A"]["invalid"] == 1
    assert report["arms"]["A"]["brier"] == 2.0
    assert report["itt_sensitivity_brier"]["uniform"]["A"] == pytest.approx(2 / 3)


async def test_intent_failure_never_invokes_model(tmp_path):
    class Broken(Store):
        async def begin(self, *args):
            raise OSError("database unavailable")

    async def invoke(**kwargs):
        pytest.fail("provider must not be called")

    data = materials()
    result = await campaign(
        {"protocol": data["protocol"], "cases": data["development"][:1]},
        tmp_path / "broken",
        CONFIG,
        Broken(),
        "test:owner",
        invoke,
        intervention_cases=0,
    )
    assert all(r["status"] == "invalid" for r in result["runs"])


async def test_retry_records_distinct_calls_same_logical_id(tmp_path):
    data = materials()
    artifacts = PilotArtifacts(
        tmp_path,
        experiment_id="fixture",
        case=data["development"][0],
        arm_id="B",
        configuration=CONFIG,
    )
    store = Store()
    calls = 0

    async def invoke(**kwargs):
        nonlocal calls
        calls += 1
        return response({"invalid": True} if calls == 1 else decision())

    runner = AttemptRunner(
        artifacts,
        recorder_for(artifacts, store, "test:owner", CONFIG["pricing"]),
        invoke,
        CampaignBudget(Decimal(5), Decimal("0.3"), Decimal("1.2")),
        24000,
    )
    await runner.call("final", {"evidence": [{"id": "returns", "content": {}}]}, 24000, final=True)
    assert len(store.starts) == 2
    assert store.starts[0][1] != store.starts[1][1]
    assert store.starts[0][2] == store.starts[1][2]
    assert [row[3] for row in store.starts] == [0, 1]
    assert runner.spent == 400


def test_campaign_cap_and_report_pairing():
    budget = CampaignBudget(Decimal("0.0001"), Decimal("0.3"), Decimal("1.2"))
    with pytest.raises(BudgetStop):
        budget.reserve(1000, 1000)
    with pytest.raises(ValueError, match="exactly one"):
        build_report([], materials()["labels"], [])


def test_report_rejects_duplicate_labels():
    label = materials()["labels"][0]
    with pytest.raises(ValueError, match="unique outcome"):
        build_report([], [label, label], [])


@pytest.mark.parametrize(
    "patch",
    [
        {"value": "UNKNOWN"},
        {"excess_return": float("nan")},
        {"excess_return": float("inf")},
        {"excess_return": True},
        {"case_id": ""},
    ],
)
def test_report_rejects_invalid_labels(patch):
    label = {**materials()["labels"][0], **patch}
    with pytest.raises(ValueError, match="invalid outcome"):
        build_report([], [label], [])


def test_report_rejects_empty_labels():
    with pytest.raises(ValueError, match="nonempty"):
        build_report([], [], [])


async def test_baseline_final_retries_invalid_output(tmp_path):
    data = materials()
    calls = 0

    async def invoke(**kwargs):
        nonlocal calls
        calls += 1
        return response({"invalid": True} if calls == 1 else decision())

    result = await campaign(
        {"protocol": data["protocol"], "cases": data["development"][:1]},
        tmp_path / "baseline-retry",
        CONFIG,
        Store(),
        "test:owner",
        invoke,
        intervention_cases=0,
    )
    baseline = result["runs"][0]
    assert baseline["status"] == "completed"
    assert len(baseline["calls"]) == 2
    assert [c["attempt"] for c in baseline["calls"]] == [0, 1]


def test_neutral_conclusion_flip_is_explicitly_skipped():
    neutral = {**decision(), "action": 0}
    run = {
        "status": "completed",
        "decision": neutral,
        "history": [{"role": "risk", "round": 1, "output": neutral}],
    }
    plans = intervention_plans(run, materials()["development"][0])
    flip = next(plan for plan in plans if plan["name"] == "flip_conclusion")
    assert flip["skip"] == "NEUTRAL_CONCLUSION_HAS_NO_DIRECTIONAL_REVERSE"
    assert "prefix" not in flip


def test_benchmark_dividends_are_opt_in_and_use_entry_exit_window():
    source = market()
    cases = source["sessions"][260:270]
    default = build_materials(source, cases)
    i = 100
    source["benchmark"][i + 3]["dividends"] = 2.0  # ex-date inside (entry, exit]
    source["benchmark"][i + 1]["dividends"] = 5.0  # ex-date on entry: already ex, excluded
    price_only = excess(source["asset"], source["benchmark"], i)
    total = excess(source["asset"], source["benchmark"], i, benchmark_dividends=True)
    assert total == pytest.approx(price_only - 2.0 / source["benchmark"][i + 1]["open"])
    unchanged = build_materials(source, cases)
    assert unchanged["protocol"]["tau"] == default["protocol"]["tau"]
    adjusted = build_materials(source, cases, benchmark_dividends=True)
    assert adjusted["protocol"]["tau"] != default["protocol"]["tau"]
    assert "dividends" in adjusted["protocol"]["benchmark_return"]
    assert not validate_materials(adjusted)


def test_tau_window_and_climatology_recorded():
    source = market()
    cases = source["sessions"][260:270]
    trailing = build_materials(source, cases, tau_windows=50)
    protocol = trailing["protocol"]
    assert protocol["tau_calibration_windows"] == 50
    assert set(protocol["climatology"]) == {"UP", "FLAT", "DOWN"}
    assert sum(protocol["climatology"].values()) == pytest.approx(1)
    with pytest.raises(ValueError, match="insufficient calibration"):
        build_materials(source, cases, tau_windows=10_000)


def test_flip_keeps_rationale_and_replacement_uses_neutral_prose():
    out = {**decision(), "rationale": "momentum and range position support upside"}
    run = {
        "status": "completed",
        "decision": out,
        "history": [{"role": "bull", "round": 0, "output": out}],
    }
    plans = {p["name"]: p for p in intervention_plans(run, materials()["development"][0])}
    flipped = plans["flip_conclusion"]["prefix"][-1]["output"]
    assert flipped["rationale"] == out["rationale"]
    assert (flipped["p_up"], flipped["p_down"], flipped["action"]) == (0.1, 0.6, -1)
    replaced = plans["replace_contribution"]["prefix"][-1]
    assert set(replaced) == {"role", "round", "message"}
    assert replaced["message"] and replaced["message"] in " ".join(NEUTRAL_CORPUS) * 4
    assert not any(ch.isdigit() for ch in replaced["message"])


async def test_sample_temperature_applies_only_to_b_samples(tmp_path):
    data = materials()
    seen = []

    async def invoke(**kwargs):
        payload = json.loads(kwargs["user"])
        seen.append((payload.get("role"), payload["instruction"], kwargs["temperature"]))
        return response(decision([payload["evidence"][0]["id"]]))

    await campaign(
        {"protocol": data["protocol"], "cases": data["development"][:1]},
        tmp_path / "campaign",
        {**CONFIG, "sample_temperature": 0.8},
        Store(),
        "test:owner",
        invoke,
        intervention_cases=0,
    )
    samples = [t for role, instruction, t in seen if instruction == "independent assessment"]
    others = [t for role, instruction, t in seen if instruction != "independent assessment"]
    assert samples and set(samples) == {0.8}
    assert others and set(others) == {0.2}


def test_report_references_skill_and_b_mean():
    data = materials()
    label = {**data["labels"][0], "value": "UP"}
    sample = lambda p_up, p_down, action: {  # noqa: E731
        "role": "sample",
        "round": 0,
        "output": {**decision(), "p_up": p_up, "p_flat": 0.2, "p_down": p_down, "action": action},
    }
    b_history = [sample(0.7, 0.1, 1), sample(0.1, 0.7, -1)]
    runs = [
        {
            "case_id": label["case_id"],
            "arm": arm,
            "status": "completed",
            "decision": {**decision(), "p_up": 0.8, "p_flat": 0.1, "p_down": 0.1},
            "history": b_history if arm == "B" else [],
            "calls": [],
        }
        for arm in "ABCD"
    ]
    climatology = {"UP": 0.25, "FLAT": 0.5, "DOWN": 0.25}
    report = build_report(runs, [label], [], climatology=climatology)
    assert report["references_brier"]["uniform"] == pytest.approx(2 / 3)
    clim = 0.75**2 + 0.5**2 + 0.25**2
    assert report["references_brier"]["climatology"] == pytest.approx(clim)
    arm_brier = 0.2**2 + 0.1**2 + 0.1**2
    assert report["brier_skill_vs_climatology"]["A"] == pytest.approx(1 - arm_brier / clim)
    b_mean = sample_mean_run(runs[1])["decision"]
    assert b_mean["p_up"] == pytest.approx(0.4) == b_mean["p_down"]
    assert b_mean["action"] == 0
    assert "B-mean" in report["variants"] and "B-mean-B" in report["paired_brier"]
    assert report["b_sample_action_disagreement"] == 1.0
    assert report["reliability"]["A"]["UP"] == {"mean_predicted": 0.8, "observed": 1.0}
    with pytest.raises(ValueError, match="climatology"):
        build_report(runs, [label], [], climatology={"UP": 0.5, "FLAT": 0.5, "DOWN": 0.5})
