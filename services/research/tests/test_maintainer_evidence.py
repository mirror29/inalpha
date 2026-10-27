"""Maintainer checks operate on copies of frozen fixtures, never provider/database clients."""

import copy
import importlib.util
import json
from pathlib import Path

import pytest

from inalpha_research.pilot.artifacts import digest
from inalpha_research.pilot.campaign import campaign
from inalpha_research.pilot.materials import build_materials, write_private
from tests.test_four_arm_pilot import CONFIG, Store, decision, market, response

spec = importlib.util.spec_from_file_location(
    "maintainer_evidence", Path(__file__).parents[1] / "scripts" / "verify_pilot_materials.py"
)
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


def states(report):
    return {c["check"]: c["status"] for c in report["checks"]}


@pytest.fixture
async def packet(tmp_path):
    source = market()
    data = build_materials(source, source["sessions"][260:261])
    inputs = {
        "protocol": data["protocol"],
        "cases": data["development"],
        "outcomes_hash": digest(data["labels"]),
    }
    materials = tmp_path / "materials"
    materials.mkdir()
    for name, value in {
        "market": source,
        "materials": data,
        "inputs": inputs,
        "outcomes": data["labels"],
        "annotations": data["annotations"],
    }.items():
        write_private(materials / f"{name}.json", value)

    async def invoke(**kwargs):
        return response(decision([json.loads(kwargs["user"])["evidence"][0]["id"]]))

    directory = tmp_path / "campaign"
    await campaign(
        inputs,
        directory,
        CONFIG,
        Store(),
        "private-owner-do-not-export",
        invoke,
        intervention_cases=1,
    )
    write_private(directory / "report.json", {"original": True})
    return directory, materials


def rewrite(path, change):
    value = json.loads(path.read_text())
    change(value)
    path.write_text(json.dumps(value))


def fingerprint(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}


async def test_valid_packet_readonly_and_exclusive_output(packet, tmp_path, monkeypatch):
    import socket

    def forbidden(*args, **kwargs):
        raise AssertionError("network forbidden")

    monkeypatch.setattr(socket, "socket", forbidden)
    before = fingerprint(tmp_path)
    report = audit.packet_audit(*packet)
    assert report["status"] == "pass", [c for c in report["checks"] if c["status"] != "pass"]
    assert report["formal_ready"] is False
    assert fingerprint(tmp_path) == before
    assert "private-owner" not in json.dumps(report)
    assert "fixture assessment" not in json.dumps(report)
    out = tmp_path / "audit"
    audit.write_report(report, out, list(packet))
    assert (out / "verification.json").stat().st_mode & 0o777 == 0o600
    with pytest.raises(FileExistsError):
        audit.write_report(report, out, list(packet))
    with pytest.raises(ValueError):
        audit.write_report(report, packet[0] / "audit", list(packet))


@pytest.mark.parametrize(
    "mutation,check",
    [
        ("hash", "outcome_commitment"),
        ("missing", "run_1_result"),
        ("duplicate", "unique_runs"),
        ("call", "global_call_uniqueness"),
        ("unknown", "run_1_usage"),
        ("incomplete", "run_1_validator"),
    ],
)
async def test_packet_failures(packet, mutation, check):
    directory, materials = packet
    results = json.loads((directory / "results.json").read_text())
    run = directory / results["runs"][0]["run_id"]
    if mutation == "hash":
        rewrite(materials / "outcomes.json", lambda v: v[0].update(value="DOWN"))
    elif mutation == "missing":
        (run / "result.json").unlink()
    elif mutation == "duplicate":
        rewrite(directory / "results.json", lambda v: v["runs"].append(v["runs"][0]))
    else:
        events = [json.loads(x) for x in (run / "calls.jsonl").read_text().splitlines()]
        if mutation == "call":
            events.append(events[0])
        elif mutation == "unknown":
            for e in events:
                if e["event"] == "settled":
                    e["usage"] = {"usage_status": "unknown"}
        else:
            events = [e for e in events if e["event"] != "settled"]
        (run / "calls.jsonl").write_text("\n".join(json.dumps(e) for e in events))
    assert states(audit.packet_audit(*packet))[check] in {"blocked", "unknown"}


async def test_absent_climatology_not_invented(packet):
    for root, name in [(packet[0], "campaign"), (packet[1], "inputs"), (packet[1], "materials")]:
        rewrite(root / f"{name}.json", lambda v: v["protocol"].pop("climatology"))
    assert states(audit.packet_audit(*packet))["climatology_available"] == "unknown"


def data_fixture():
    source = market()
    source["retrieved_at"] = "2026-10-27T00:00:00Z"
    for name in ("asset", "benchmark"):
        for row in source[name]:
            row.update(dividends=0, splits=0)
    config = {
        "expected_sessions": source["sessions"],
        "market_hash": digest(source),
        "candidate_sessions": source["sessions"][260:262],
        "lookback_sessions": 252,
        "calibration_windows": 252,
        "horizon_sessions": 5,
        "exposures": [],
        "protocol_frozen": False,
        "requires_historical_membership": False,
        "symbols": {"asset": "FIXTURE", "benchmark": "REFERENCE"},
    }
    return source, config


def test_data_coverage_allows_overlapping_warmup():
    source, config = data_fixture()
    report = audit.data_audit(source, config)
    assert states(report)["candidate_coverage"] == "pass"
    assert states(report)["protocol_freeze"] == "blocked"
    assert states(report)["historical_availability"] == "unknown"
    assert report["formal_ready"] is False


@pytest.mark.parametrize(
    "kind", ["development_decision", "development_outcome", "calibration_analysis"]
)
def test_exposure_excludes_candidates(kind):
    source, config = data_fixture()
    config["exposures"] = [
        {"kind": kind, "start": source["sessions"][262], "end": source["sessions"][265]}
    ]
    report = audit.data_audit(source, config)
    assert states(report)["candidate_coverage"] == "blocked"
    assert next(c for c in report["checks"] if c["check"] == "candidate_coverage")["excluded"] == 2


@pytest.mark.parametrize(
    "mode,check",
    [
        ("alignment", "asset_alignment"),
        ("action", "asset_dividends"),
        ("prices", "asset_prices"),
        ("hash", "source_hash"),
    ],
)
def test_data_gaps(mode, check):
    source, config = data_fixture()
    if mode == "alignment":
        source["asset"].pop()
    elif mode == "action":
        source["asset"][0].pop("dividends")
    elif mode == "prices":
        source["asset"][0]["high"] = 1
    else:
        config["market_hash"] = "wrong"
    assert states(audit.data_audit(source, config))[check] in {"blocked", "unknown"}


def test_historical_membership_never_uses_future_list():
    source, config = data_fixture()
    config["requires_historical_membership"] = True
    assert states(audit.data_audit(source, config))["historical_membership_evidence"] == "blocked"
    membership = {"source": "fixture archive", "as_of": "2026-10-27", "members": ["FIXTURE"]}
    config["membership"] = {**membership, "hash": digest(membership)}
    assert states(audit.data_audit(source, config))["historical_membership_evidence"] == "blocked"
    membership["as_of"] = source["sessions"][0]
    config["membership"] = {**membership, "hash": digest(membership)}
    assert states(audit.data_audit(source, config))["historical_membership_evidence"] == "pass"


def test_data_does_not_modify_inputs():
    source, config = data_fixture()
    original = copy.deepcopy((source, config))
    audit.data_audit(source, config)
    assert (source, config) == original


async def test_symlink_artifact_refused(packet, tmp_path):
    target = tmp_path / "outside.json"
    target.write_text('{"private": "must not be read"}')
    path = packet[0] / "report.json"
    path.unlink()
    path.symlink_to(target)
    report = audit.packet_audit(*packet)
    assert states(report)["original_report"] == "blocked"
    assert "must not be read" not in json.dumps(report)


async def test_usage_projection_mismatch(packet):
    rewrite(
        packet[0] / "results.json",
        lambda v: v["runs"][0]["calls"][0]["usage"].update(input_tokens=999999),
    )
    assert states(audit.packet_audit(*packet))["run_1_usage_projection"] == "blocked"


async def test_malformed_top_level_is_blocked(packet):
    (packet[0] / "campaign.json").write_text("[]")
    assert states(audit.packet_audit(*packet))["packet_structure"] == "blocked"


@pytest.mark.parametrize(
    "offset,reason",
    [(250, "feature_lookback"), (257, "calibration_coverage"), (299, "outcome_coverage")],
)
def test_exact_coverage_boundaries(offset, reason):
    source, config = data_fixture()
    config["candidate_sessions"] = [source["sessions"][offset]]
    check = next(
        c for c in audit.data_audit(source, config)["checks"] if c["check"] == "candidate_1"
    )
    assert reason in check["reasons"]


async def test_packet_cli_writes_reports_without_network(packet, tmp_path, monkeypatch):
    import socket
    import sys

    def forbidden(*args, **kwargs):
        raise AssertionError("network forbidden")

    monkeypatch.setattr(socket, "socket", forbidden)
    output = tmp_path / "cli-output"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "verify",
            "packet",
            "--campaign",
            str(packet[0]),
            "--materials",
            str(packet[1]),
            "--output",
            str(output),
        ],
    )
    before = [fingerprint(root) for root in packet]
    with pytest.raises(SystemExit) as exc:
        audit.main()
    assert exc.value.code == 0
    assert (output / "verification.md").exists()
    assert before == [fingerprint(root) for root in packet]


def test_invalid_dates_block_data():
    source, config = data_fixture()
    source["retrieved_at"] = "not-a-date"
    assert states(audit.data_audit(source, config))["data_structure"] == "blocked"
