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
    materials["development"].append(case())
    assert "duplicate development case" in validate_materials(materials)
    materials["development"].pop()
    materials["annotations"][0]["criteria"] = {}
    assert "invalid annotations provenance" in validate_materials(materials)
    materials["annotations"][0]["criteria"] = {"factual_errors": 0}
    materials["labels"][0]["value"] = {}
    assert "invalid labels provenance" in validate_materials(materials)
    materials["labels"][0]["value"] = 0
    assert validate_materials(materials) == []
    del materials["labels"][0]["value"]
    assert "invalid labels provenance" in validate_materials(materials)


def test_model_input_keeps_original_frozen_snapshot(tmp_path):
    original = case()
    artifacts = PilotArtifacts(
        tmp_path, experiment_id="e", case=original, arm_id="A", configuration={}
    )
    frozen_input = artifacts.model_input()
    original["evidence"][0]["content"] = "future outcome"
    artifacts.case["evidence"][0]["content"] = "another future outcome"
    assert artifacts.model_input() == frozen_input
    assert json.loads(frozen_input) == json.loads((artifacts.path / "evidence.json").read_text())


@pytest.mark.parametrize(
    "configuration,provider,model",
    [
        ({}, "openai", "m"),
        ({"provider": "openai", "model": ""}, "openai", "m"),
        ({"provider": "openai", "model": "m"}, "", "m"),
        ({"provider": "openai", "model": "m"}, "openai", ""),
        ({"provider": "openai", "model": "m"}, "anthropic", "m"),
        ({"provider": "openai", "model": "m"}, "openai", "other"),
    ],
)
def test_pilot_rejects_missing_or_conflicting_model_before_factory(
    tmp_path, monkeypatch, configuration, provider, model
):
    from unittest.mock import Mock

    from inalpha_research.pilot import session

    factory = Mock()
    monkeypatch.setattr(session, "build_llm_client", factory)
    artifacts = PilotArtifacts(
        tmp_path, experiment_id="e", case=case(), arm_id="A", configuration=configuration
    )
    with pytest.raises(ValueError, match="pilot"):
        session.pilot_client(
            artifacts,
            auth_sub="owner",
            store=object(),
            provider=provider,
            model=model,
            api_key="test-only",
        )
    factory.assert_not_called()


def test_pilot_uses_frozen_configuration_despite_caller_mutation(tmp_path, monkeypatch):
    from unittest.mock import Mock

    from inalpha_research.pilot import session

    configuration = {"provider": "OpenAI", "model": "m"}
    artifacts = PilotArtifacts(
        tmp_path, experiment_id="e", case=case(), arm_id="A", configuration=configuration
    )
    configuration["model"] = "changed"
    artifacts.configuration["model"] = "also-changed"
    factory = Mock()
    monkeypatch.setattr(session, "build_llm_client", factory)
    result = session.pilot_client(
        artifacts,
        auth_sub="owner",
        store=object(),
        provider="OPENAI",
        model="m",
        api_key="test-only",
    )
    assert result is factory.return_value
    assert factory.call_args.kwargs["provider"] == "openai"
    assert factory.call_args.kwargs["model"] == "m"
    assert artifacts.configuration["model"] == "m"
