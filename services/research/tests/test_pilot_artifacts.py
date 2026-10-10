"""Frozen evidence does not gain permission to see future outcomes through hashing."""

import json

import pytest

from inalpha_research.pilot.artifacts import (
    PilotArtifacts,
    digest,
    freeze,
    inventory,
    safe_json,
    validate_bundle,
    validate_materials,
)


def case():
    return freeze(
        "c1",
        "2026-05-01T21:00:00Z",
        [
            {
                "id": "price1",
                "kind": "price",
                "content": "close=100",
                "source": "synthetic",
                "available_at": "2026-05-01T20:01:00Z",
                "session_close": "2026-05-01T20:00:00Z",
            }
        ],
    )


def test_hash_future_and_unfinished_session():
    bundle = case()
    assert validate_bundle(bundle) == []
    bundle["evidence"][0]["content"] = "close=200"
    assert "bundle hash mismatch" in validate_bundle(bundle)
    bundle = case()
    bundle["decision_at"] = "2026-05-01T19:00:00Z"
    assert "unfinished session" in validate_bundle(bundle)
    assert "future evidence" in validate_bundle(bundle)


def test_rehashed_outcome_injection_rejected():
    bundle = case()
    bundle["evidence"][0]["outcome"] = "future"
    bundle["hash"] = digest({k: v for k, v in bundle.items() if k != "hash"})
    assert "invalid evidence schema" in validate_bundle(bundle)


def test_artifacts_preserve_partial_run_and_private_permissions(tmp_path):
    artifacts = PilotArtifacts(
        tmp_path,
        experiment_id="experiment",
        case=case(),
        arm_id="A",
        configuration={"model": "fake"},
    )
    artifacts.event({"call_id": "c", "event": "started"})
    assert not (artifacts.path / "result.json").exists()
    assert (artifacts.path / "manifest.json").stat().st_mode & 0o777 == 0o600
    assert "outcome" not in artifacts.model_input()
    artifacts.finish({"decision": "neutral"})
    with pytest.raises(FileExistsError):
        artifacts.finish({})


@pytest.mark.parametrize(
    "value", [{"api_key": "secret"}, {"prompt": "Bearer abcdefghijklmnopqrstuvwxyz"}]
)
def test_credentials_rejected(value):
    with pytest.raises(ValueError):
        safe_json(value)


def test_inventory_exports_only_field_coverage(tmp_path):
    path = tmp_path / "private.json"
    path.write_text(json.dumps({"thesis": "private text", "briefs": [], "debate_log": []}))
    report = inventory([path])
    assert report[0]["complete_research_records"] == 1
    assert "private text" not in json.dumps(report)


def test_no_silent_protocol_defaults_or_sample_overlap():
    issues = validate_materials({"development": [case()], "formal": [case()]})
    assert "protocol missing benchmark" in issues
    assert "development/formal overlap: case_id" in issues
    assert "missing agreed session schedule" in issues


def test_run_validation_distinguishes_local_output_from_durable_ledger(tmp_path):
    from inalpha_research.pilot.artifacts import validate_run

    artifacts = PilotArtifacts(
        tmp_path, experiment_id="e", case=case(), arm_id="A", configuration={}
    )
    artifacts.event({"event": "started", "call_id": "c"})
    assert "incomplete call events" in validate_run(artifacts.path)
    artifacts.event({"event": "settled", "call_id": "c", "ledger_settled": False})
    artifacts.finish({"decision": "neutral"})
    assert "ledger settlement unconfirmed" in validate_run(artifacts.path)


def test_materials_require_actual_label_content_not_only_metadata():
    protocol = {
        key: "fixture"
        for key in (
            "version",
            "asset",
            "calendar",
            "timezone",
            "benchmark",
            "horizon",
            "entry_timing",
            "adjustment",
            "transaction_costs",
            "decision_semantics",
            "probability_semantics",
            "neutral_treatment",
            "short_treatment",
            "thresholds",
        )
    }
    raw = {"close": 100}
    bar = {"session": "2026-05-01", "source": "synthetic", "raw": raw, "hash": digest(raw)}
    materials = {
        "protocol": protocol,
        "development": [case()],
        "formal": [],
        "market": {"sessions": ["2026-05-01"], "asset": [bar], "benchmark": [bar]},
        "labels": [
            {
                "case_id": "c1",
                "protocol_version": "fixture",
                "provenance": "synthetic",
                "code_version": "v1",
                "value": "neutral",
            }
        ],
        "annotations": [
            {
                "case_id": "c1",
                "protocol_version": "fixture",
                "provenance": "synthetic",
                "rubric_version": "v1",
                "blinded": True,
                "evidence_ids": ["price1"],
                "criteria": {"factual_errors": 0},
            }
        ],
    }
    assert validate_materials(materials) == []
    del materials["labels"][0]["value"]
    assert "invalid labels provenance" in validate_materials(materials)
