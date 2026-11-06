"""Offline rescoring provenance and per-case budget diagnostics (#206 v0.3 §1)."""

import json
import sys

import pytest

from inalpha_research.pilot.artifacts import digest
from inalpha_research.pilot.budget import budget_diagnostics
from inalpha_research.pilot.campaign import campaign
from inalpha_research.pilot.rescore import hash_tree, main, rescore, write_rescore
from tests.test_four_arm_pilot import CONFIG, Store, decision, materials, response


def call(tokens, *, attempt=0, known=True, error=None):
    usage = (
        {"usage_status": "known", "input_tokens": tokens - 100, "output_tokens": 100}
        if known
        else {"usage_status": "unknown"}
    )
    return {"attempt": attempt, "usage": usage, "error": error}


def run(case, arm, calls, *, status="completed", reason=None, units=3):
    return {
        "case_id": case,
        "arm": arm,
        "status": status,
        "reason": reason,
        "calls": calls,
        "completed_units": units,
        "tokens_charged": sum(c["usage"].get("input_tokens", 0) for c in calls),
    }


def test_budget_target_is_separate_from_cap_and_unknown_usage_is_excluded():
    runs = [
        run("c1", "B", [call(9000)]),
        run("c2", "B", [call(6000), call(4000, attempt=1, error="ValueError")]),
        run("c3", "B", [call(15000)], reason="TOKEN_BUDGET_OR_UNKNOWN_USAGE", units=2),
        run(
            "c4", "B", [call(8000), call(0, known=False)], status="invalid", reason="UNKNOWN_USAGE"
        ),
    ]
    report = budget_diagnostics(runs, caps={"B": 20000}, targets={"B": 10000})
    b = report["arms"]["B"]
    assert b["matching_target_tokens"] == 10000 and b["safety_cap_tokens"] == 20000
    # c4 has an unknown attempt: excluded from matching, never zero-filled.
    assert b["cases_excluded_unknown_usage"] == 1 and b["unknown_attempts"] == 1
    assert b["usage"]["median"] == 10000 and b["usage"]["iqr"] == pytest.approx(3000)
    assert b["share_cases_within_25_percent"] == pytest.approx(2 / 3)
    assert b["matching_achieved"] is False  # median ok, but only 2/3 of cases within ±25%
    assert b["retries"] == 1 and b["failed_attempts"] == 1 and b["attempts"] == 6
    assert b["token_budget_stops"] == 1 and b["accounting_stops"] == 1
    assert b["invalid_runs"] == 1 and b["invalid_rate"] == 0.25
    assert b["completed_units"] == {"2": 1, "3": 3}
    c4 = next(c for c in b["per_case"] if c["case_id"] == "c4")
    assert c4["known_tokens"] == 8000 and c4["ratio_to_target"] is None


@pytest.mark.parametrize(
    ("tokens", "achieved"),
    [(1100, True), (900, True), (1101, False), (899, False)],
)
def test_median_boundary_is_inclusive_and_exact(tokens, achieved):
    runs = [run(f"c{i}", "B", [call(tokens)]) for i in range(5)]
    b = budget_diagnostics(runs, caps={}, targets={"B": 1000})["arms"]["B"]
    assert b["share_cases_within_25_percent"] == 1.0
    assert b["matching_achieved"] is achieved


@pytest.mark.parametrize(("tokens", "inside"), [(1250, 1.0), (750, 1.0), (1251, 0.0), (749, 0.0)])
def test_case_boundary_is_inclusive_and_exact(tokens, inside):
    b = budget_diagnostics([run("c1", "B", [call(tokens)])], caps={}, targets={"B": 1000})
    assert b["arms"]["B"]["share_cases_within_25_percent"] == inside


def test_budget_without_declared_target_omits_ratios_and_verdict():
    report = budget_diagnostics([run("c1", "A", [call(600)])], caps={"A": 4000}, targets={})
    a = report["arms"]["A"]
    assert a["matching_target_tokens"] is None and a["matching_achieved"] is None
    assert a["per_case"][0]["ratio_to_target"] is None
    assert a["per_case"][0]["ratio_to_cap"] == pytest.approx(0.15)


async def saved_campaign(tmp_path):
    data = materials()

    async def invoke(**kwargs):
        payload = json.loads(kwargs["user"])
        return response(decision([payload["evidence"][0]["id"]]))

    directory = tmp_path / "campaign"
    await campaign(
        {
            "protocol": data["protocol"],
            "cases": data["development"][:2],
            "outcomes_hash": digest(data["labels"][:2]),
        },
        directory,
        CONFIG,
        Store(),
        "test:owner",
        invoke,
        intervention_cases=0,
    )
    (directory / "report.json").write_text('{"original": true}')
    outcomes = tmp_path / "outcomes.json"
    outcomes.write_text(json.dumps(data["labels"][:2]))
    return directory, outcomes


async def test_rescore_writes_separate_versioned_report_and_preserves_originals(tmp_path):
    directory, outcomes = await saved_campaign(tmp_path)
    before = hash_tree(directory)
    value = rescore(directory, outcomes, targets={"A": None, "B": 400, "C": 400, "D": 400})
    output = tmp_path / "rescore-v1"
    write_rescore(value, output, [directory])
    assert hash_tree(directory) == before
    assert json.loads((directory / "report.json").read_text()) == {"original": True}
    saved = json.loads((output / "rescore.json").read_text())
    assert saved["version"] == "pilot-rescore-v1"
    assert saved["sources"]["campaign_files"] == before
    assert saved["sources"]["original_report_sha256"] is not None
    assert saved["checks"] == {
        "outcome_commitment_verified": True,
        "campaign_files_unchanged": True,
    }
    assert saved["parameters"]["matching_target_source"] == "rescoring parameter"
    assert saved["parameters"]["safety_cap_tokens"] == {
        "A": 4000,
        "B": 24000,
        "C": 24000,
        "D": 24000,
    }
    assert saved["report"]["complete_case_count"] == 2
    assert saved["budget_diagnostics"]["arms"]["B"]["usage"]["median"] > 0
    with pytest.raises(FileExistsError):
        write_rescore(value, output, [directory])
    with pytest.raises(ValueError, match="outside"):
        write_rescore(value, directory / "nested", [directory])


async def test_rescore_rejects_changed_outcomes_and_symlinks(tmp_path):
    directory, outcomes = await saved_campaign(tmp_path)
    labels = json.loads(outcomes.read_text())
    labels[0]["value"] = "DOWN" if labels[0]["value"] != "DOWN" else "UP"
    outcomes.write_text(json.dumps(labels))
    with pytest.raises(ValueError, match="outcome hash"):
        rescore(directory, outcomes)
    (directory / "link.json").symlink_to(outcomes)
    with pytest.raises(ValueError, match="symlinks"):
        hash_tree(directory)


async def test_rescore_cli_and_undeclared_target(tmp_path, monkeypatch, capsys):
    directory, outcomes = await saved_campaign(tmp_path)
    output = tmp_path / "cli"
    monkeypatch.setattr(sys, "argv", ["rescore", str(directory), str(outcomes), str(output)])
    main()
    saved = json.loads((output / "rescore.json").read_text())
    assert saved["parameters"]["matching_target_source"] == "not declared"
    assert saved["budget_diagnostics"]["arms"]["D"]["matching_achieved"] is None
    assert "pilot-rescore-v1" in capsys.readouterr().out
    monkeypatch.setattr(
        sys, "argv", ["rescore", str(directory), str(outcomes), str(output), "--target-tokens", "0"]
    )
    with pytest.raises(SystemExit):
        main()


async def test_campaign_report_includes_budget_diagnostics(tmp_path, monkeypatch):
    from inalpha_research.pilot import campaign as campaign_module

    directory, outcomes = await saved_campaign(tmp_path)
    (directory / "report.json").unlink()
    monkeypatch.setattr(sys, "argv", ["campaign", "report", str(directory), str(outcomes)])
    campaign_module.main()
    report = json.loads((directory / "report.json").read_text())
    arms = report["budget_diagnostics"]["arms"]
    assert set(arms) == set("ABCD")
    assert arms["A"]["safety_cap_tokens"] == 4000 and arms["B"]["matching_target_tokens"] is None
