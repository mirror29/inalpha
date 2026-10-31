"""Intervention selection and accounting (#206 v0.3 §4)."""

import json

import pytest

from inalpha_research.pilot.artifacts import digest
from inalpha_research.pilot.campaign import campaign
from inalpha_research.pilot.interventions import INTERVENTIONS, accounting, select_cases
from inalpha_research.pilot.report import build_report
from tests.test_four_arm_pilot import CONFIG, Store, decision, materials, response


def d_run(case, status="completed"):
    return {"case_id": case, "arm": "D", "status": status}


def test_seeded_selection_uses_only_valid_d_runs_and_reports_shortfall():
    runs = [d_run("c1"), d_run("c2", "invalid"), d_run("c3"), d_run("c4")]
    ids = ["c1", "c2", "c3", "c4"]
    first = select_cases(runs, ids, rule="seeded_random", size=2, seed=206)
    again = select_cases(runs, ids, rule="seeded_random", size=2, seed=206)
    assert first == again and len(first["selected"]) == 2
    assert "c2" not in first["selected"] and first["valid_d_pool"] == 3
    assert first["full_pool"] == 4 and first["shortfall"] == 0
    assert first["conditional_on"] == "valid D base run"
    short = select_cases(runs, ids, rule="seeded_random", size=40, seed=1)
    assert short["selected"] == ["c1", "c3", "c4"] and short["shortfall"] == 37
    legacy = select_cases(runs, ids, rule="first_n", size=2)
    assert legacy["selected"] == ["c1", "c2"] and legacy["shortfall"] is None
    with pytest.raises(ValueError, match="seed"):
        select_cases(runs, ids, rule="seeded_random", size=2)
    with pytest.raises(ValueError, match="rule"):
        select_cases(runs, ids, rule="best_cases", size=2)


def replay(case, name, status="completed"):
    return {"case_id": case, "status": status, "replay": {"name": name}}


def test_accounting_counts_skips_failures_and_valid_pairs_with_denominators():
    runs = [d_run("c1"), d_run("c2"), d_run("c3")]
    full = [replay("c1", name) for name in INTERVENTIONS]
    neutral_uncited = [
        replay("c2", "delete_contribution"),
        replay("c2", "replace_contribution"),
        {
            "case_id": "c2",
            "name": "flip_conclusion",
            "skip": "NEUTRAL_CONCLUSION_HAS_NO_DIRECTIONAL_REVERSE",
        },
        replay("c2", "downstream_noise"),
        {"case_id": "c2", "name": "remove_evidence", "skip": "NO_CITED_EVIDENCE"},
    ]
    broken_noise = [
        replay("c3", "delete_contribution"),
        replay("c3", "replace_contribution"),
        replay("c3", "flip_conclusion"),
        replay("c3", "downstream_noise", status="invalid"),
        replay("c3", "remove_evidence"),
        {"case_id": "c3", "name": "remove_placebo_evidence", "skip": "NO_UNCITED_EVIDENCE"},
        replay("c3", "round_zero_noise"),
    ]
    selection = {"rule": "seeded_random", "seed": 1, "full_pool": 5, "valid_d_pool": 3}
    report = accounting(
        full + neutral_uncited + broken_noise, runs, {**selection, "selected": ["c1", "c2", "c3"]}
    )
    table = report["interventions"]
    assert report["selected_cases"] == 3 and report["selection"]["full_pool"] == 5
    flip = table["flip_conclusion"]
    assert (flip["skipped"], flip["attempted"], flip["valid"]) == (1, 2, 2)
    assert flip["skip_rate"] == pytest.approx(1 / 3)
    # c3's downstream noise failed, so only c1 forms a valid flip-noise pair.
    assert flip["valid_intervention_noise_pairs"] == 1
    noise = table["downstream_noise"]
    assert noise["failed_or_invalid"] == 1 and noise["failure_rate"] == pytest.approx(1 / 3)
    assert noise["valid_intervention_noise_pairs"] is None
    placebo = table["remove_placebo_evidence"]
    assert placebo["skip_reasons"] == {
        "NOT_PLANNED:NO_CITED_EVIDENCE": 1,
        "NO_UNCITED_EVIDENCE": 1,
    }
    assert table["remove_evidence"]["valid_intervention_noise_pairs"] == 2
    assert table["round_zero_noise"]["skip_reasons"] == {"NOT_PLANNED:NO_CITED_EVIDENCE": 1}


def test_invalid_base_skip_applies_to_every_intervention():
    runs = [d_run("c1", "invalid")]
    report = accounting(
        [{"case_id": "c1", "name": "interventions", "skip": "INVALID_BASE_RUN"}],
        runs,
        {"rule": "first_n", "full_pool": 1, "valid_d_pool": 0, "selected": ["c1"]},
    )
    assert all(
        row["skip_reasons"] == {"INVALID_BASE_RUN": 1} and row["attempted"] == 0
        for row in report["interventions"].values()
    )


async def test_campaign_records_seeded_selection_and_report_accounting(tmp_path):
    data = materials()

    async def invoke(**kwargs):
        payload = json.loads(kwargs["user"])
        return response(decision([payload["evidence"][0]["id"]]))

    result = await campaign(
        {
            "protocol": data["protocol"],
            "cases": data["development"][:2],
            "outcomes_hash": digest(data["labels"][:2]),
        },
        tmp_path / "campaign",
        {**CONFIG, "intervention_selection": {"rule": "seeded_random", "size": 5, "seed": 206}},
        Store(),
        "test:owner",
        invoke,
    )
    selection = result["intervention_selection"]
    assert selection["valid_d_pool"] == 2 and selection["shortfall"] == 3
    assert set(selection["selected"]) == {
        data["development"][0]["case_id"],
        data["development"][1]["case_id"],
    }
    saved = json.loads((tmp_path / "campaign" / "results.json").read_text())
    assert saved["intervention_selection"] == selection
    report = build_report(
        result["runs"], data["labels"][:2], result["replays"], selection=selection
    )
    accounting_report = report["intervention_accounting"]
    assert accounting_report["selected_cases"] == 2
    assert accounting_report["interventions"]["delete_contribution"]["attempted"] == 2
    assert (
        accounting_report["interventions"]["delete_contribution"]["valid_intervention_noise_pairs"]
        == 2
    )


@pytest.mark.parametrize(
    "selection",
    [
        {"rule": "seeded_random", "size": 2},
        {"rule": "seeded_random", "size": 2, "seed": "206"},
        {"rule": "best_cases", "size": 2},
        {"rule": "first_n", "size": -1},
        {"rule": "first_n", "size": 1.5},
    ],
)
async def test_invalid_selection_fails_before_any_provider_call(tmp_path, selection):
    data = materials()
    calls = []

    async def invoke(**kwargs):
        calls.append(kwargs)
        return response(decision([json.loads(kwargs["user"])["evidence"][0]["id"]]))

    with pytest.raises(ValueError, match=r"intervention|seed"):
        await campaign(
            {"protocol": data["protocol"], "cases": data["development"][:2]},
            tmp_path / "campaign",
            {**CONFIG, "intervention_selection": selection},
            Store(),
            "test:owner",
            invoke,
        )
    assert calls == []
    assert not (tmp_path / "campaign").exists()
