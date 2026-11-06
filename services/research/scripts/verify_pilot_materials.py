"""Offline maintainer evidence checks; never scores forecasts or contacts services."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from collections import Counter
from datetime import date as iso_date
from pathlib import Path
from typing import Any

from inalpha_research.pilot.artifacts import digest, instant, validate_materials, validate_run
from inalpha_research.pilot.materials import write_private

VERSION = "maintainer-evidence-v1"


class Audit:
    def __init__(self) -> None:
        self.checks: list[dict[str, Any]] = []
        self.files: list[dict[str, Any]] = []

    def check(self, name: str, status: str, **counts: Any) -> None:
        self.checks.append({"check": name, "status": status, **counts})

    def read(self, root: Path, name: str, label: str) -> Any:
        path = root / name
        try:
            if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
                raise ValueError("outside input")
            raw = path.read_bytes()
            value = json.loads(raw)
            self.files.append({"file": label, "sha256": hashlib.sha256(raw).hexdigest()})
            self.check(label, "pass")
            return value
        except (OSError, ValueError):
            self.check(label, "blocked")
            return None

    def result(self) -> dict[str, Any]:
        states = {c["status"] for c in self.checks}
        return {
            "version": VERSION,
            "status": "blocked"
            if "blocked" in states
            else "unknown"
            if "unknown" in states
            else "pass",
            "formal_ready": False,
            "files": self.files,
            "checks": self.checks,
            "limitations": [
                "Local evidence only; no live ledger reconciliation.",
                "No rescoring, provider calls, protocol freeze or formal readiness certification.",
            ],
        }


def packet_audit(campaign: Path, materials: Path) -> dict[str, Any]:
    audit = Audit()
    c = audit.read(campaign, "campaign.json", "campaign")
    results = audit.read(campaign, "results.json", "results")
    audit.read(campaign, "report.json", "original_report")
    inputs = audit.read(materials, "inputs.json", "inputs")
    outcomes = audit.read(materials, "outcomes.json", "outcomes")
    packet = audit.read(materials, "materials.json", "materials")
    market = audit.read(materials, "market.json", "market")
    annotations = audit.read(materials, "annotations.json", "annotations")
    if not all(isinstance(v, dict) for v in (c, results, inputs, packet)) or not isinstance(
        outcomes, list
    ):
        audit.check("packet_structure", "blocked")
        return audit.result()
    try:
        audit.check(
            "market_commitment",
            "pass" if c["protocol"].get("market_hash") == digest(market) else "blocked",
        )
        audit.check(
            "annotation_consistency",
            "pass" if annotations == packet.get("annotations") else "blocked",
        )
        audit.check(
            "raw_market_projection",
            "pass"
            if all(
                [r["raw"] for r in packet["market"][name]] == market[name]
                for name in ("asset", "benchmark")
            )
            and packet["market"]["sessions"] == market["sessions"]
            else "blocked",
        )
        issues = validate_materials(packet)
        audit.check("material_validator", "blocked" if issues else "pass", issue_count=len(issues))
        audit.check(
            "input_commitment",
            "pass"
            if c.get("inputs_hash") == results.get("inputs_hash") == digest(inputs)
            else "blocked",
        )
        audit.check(
            "outcome_commitment",
            "pass"
            if c.get("outcomes_hash")
            == results.get("outcomes_hash")
            == inputs.get("outcomes_hash")
            == digest(outcomes)
            else "blocked",
        )
        audit.check(
            "protocol_consistency",
            "pass" if c["protocol"] == inputs["protocol"] == packet["protocol"] else "blocked",
        )
        audit.check(
            "material_cases",
            "pass"
            if inputs["cases"] == packet["development"] and outcomes == packet["labels"]
            else "blocked",
        )
        clim = c["protocol"].get("climatology")
        valid_clim = (
            isinstance(clim, dict)
            and set(clim) == {"UP", "FLAT", "DOWN"}
            and all(
                type(x) in (int, float) and math.isfinite(x) and 0 <= x <= 1 for x in clim.values()
            )
            and abs(sum(clim.values()) - 1) < 1e-6
        )
        audit.check(
            "climatology_available",
            "pass" if valid_clim else "unknown" if clim is None else "blocked",
        )
        cases = {x["case_id"]: x for x in inputs["cases"]}
        base = results["runs"]
        runs = base + [r for r in results["replays"] if "skip" not in r]
        pairs = Counter((r["case_id"], r["arm"]) for r in base)
        audit.check(
            "four_arm_coverage",
            "pass" if pairs == Counter({(k, a): 1 for k in cases for a in "ABCD"}) else "blocked",
        )
        ids = [r["run_id"] for r in runs]
        audit.check("unique_runs", "pass" if len(ids) == len(set(ids)) else "blocked")
        all_calls: list[str] = []
        for index, run in enumerate(runs):
            prefix = f"run_{index + 1}"
            rid = run["run_id"]
            if not isinstance(rid, str) or Path(rid).name != rid or rid in (".", ".."):
                audit.check(prefix, "blocked")
                continue
            directory = campaign / rid
            if directory.is_symlink():
                audit.check(prefix, "blocked")
                continue
            manifest = audit.read(directory, "manifest.json", prefix + "_manifest")
            evidence = audit.read(directory, "evidence.json", prefix + "_evidence")
            terminal = audit.read(directory, "result.json", prefix + "_result")
            if not all(isinstance(x, dict) for x in (manifest, evidence, terminal)):
                continue
            events_path = directory / "calls.jsonl"
            if events_path.is_symlink():
                audit.check(prefix + "_events", "blocked")
                continue
            try:
                raw = events_path.read_bytes()
                events = [json.loads(line) for line in raw.splitlines()]
                audit.files.append(
                    {"file": prefix + "_events", "sha256": hashlib.sha256(raw).hexdigest()}
                )
                issues = validate_run(directory)
                audit.check(
                    prefix + "_validator", "blocked" if issues else "pass", issue_count=len(issues)
                )
                starts = [e.get("call_id") for e in events if e.get("event") == "started"]
                ends = [e for e in events if e.get("event") == "settled"]
                all_calls.extend(starts)
                reported = [x.get("call_id") for x in run["calls"]]
                settled = {e.get("call_id"): e for e in ends}
                audit.check(
                    prefix + "_usage_projection",
                    "pass"
                    if all(
                        x.get("usage") == settled.get(x.get("call_id"), {}).get("usage")
                        and x.get("status") == settled.get(x.get("call_id"), {}).get("status")
                        for x in run["calls"]
                    )
                    else "blocked",
                )
                audit.check(
                    prefix + "_calls",
                    "pass"
                    if Counter(reported) == Counter(starts)
                    and all(isinstance(x, str) and x for x in starts)
                    else "blocked",
                )
                unknown = sum(e.get("usage", {}).get("usage_status") != "known" for e in ends)
                audit.check(
                    prefix + "_usage",
                    "unknown" if unknown or len(ends) != len(starts) else "pass",
                    unknown_settlements=unknown,
                )
                expected_config = {**c["configuration"], "replay": run.get("replay") or None}
                linked = (
                    manifest.get("run_id") == rid
                    and manifest.get("case_id") == run["case_id"]
                    and manifest.get("arm_id") == run["arm"]
                    and manifest.get("experiment_id") == c["experiment_id"]
                    and manifest.get("configuration") == expected_config
                    and evidence == cases[run["case_id"]]
                )
                if run.get("replay"):
                    linked = linked and any(
                        b["run_id"] == run["replay"].get("base_run_id")
                        and b["case_id"] == run["case_id"]
                        and b["arm"] == "D"
                        for b in base
                    )
                audit.check(prefix + "_lineage", "pass" if linked else "blocked")
                projection = {
                    k: v
                    for k, v in run.items()
                    if k not in {"run_id", "case_id", "status", "artifact_validation", "reentry"}
                }
                audit.check(
                    prefix + "_projection",
                    "pass"
                    if terminal == {"status": run["status"], "output": projection}
                    else "blocked",
                )
            except (OSError, ValueError, TypeError, KeyError):
                audit.check(prefix + "_structure", "blocked")
        audit.check(
            "global_call_uniqueness", "pass" if len(all_calls) == len(set(all_calls)) else "blocked"
        )
        directories = {p.name for p in campaign.iterdir() if p.is_dir()}
        audit.check(
            "unindexed_run_directories",
            "pass" if directories == set(ids) else "blocked",
            count=len(directories - set(ids)),
        )
    except (KeyError, TypeError, ValueError, AttributeError):
        audit.check("packet_structure", "blocked")
    audit.check("live_ledger_reconciliation", "not_applicable")
    return audit.result()


def data_audit(market: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    audit = Audit()
    audit.files = [
        {"file": "market_canonical", "sha256": digest(market)},
        {"file": "config_canonical", "sha256": digest(config)},
    ]
    try:
        sessions = market["sessions"]
        for session in sessions:
            if iso_date.fromisoformat(session).isoformat() != session:
                raise ValueError("noncanonical session")
        schedule_ok = (
            bool(sessions)
            and sessions == sorted(set(sessions))
            and sessions == config["expected_sessions"]
        )
        audit.check("expected_sessions", "pass" if schedule_ok else "blocked")
        audit.check(
            "source_hash",
            "pass"
            if config.get("market_hash") == digest(market)
            else "unknown"
            if not config.get("market_hash")
            else "blocked",
        )
        audit.check(
            "source_metadata",
            "pass" if market.get("source") and market.get("retrieved_at") else "unknown",
        )
        # A historical retrieval is not evidence of archival availability.
        audit.check("historical_availability", "unknown")
        audit.check(
            "session_close_coverage",
            "pass" if all(market.get("session_closes", {}).get(s) for s in sessions) else "unknown",
        )
        for close in market.get("session_closes", {}).values():
            instant(close)
        if market.get("retrieved_at"):
            instant(market["retrieved_at"])
        for name in ("asset", "benchmark"):
            rows = market[name]
            aligned = [r["session"] for r in rows] == sessions
            audit.check(name + "_alignment", "pass" if aligned else "blocked")
            valid = all(
                all(
                    type(r.get(k)) in (int, float) and math.isfinite(r[k]) and r[k] > 0
                    for k in ("open", "high", "low", "close")
                )
                and type(r.get("volume")) in (int, float)
                and math.isfinite(r["volume"])
                and r["volume"] >= 0
                and r["low"]
                <= min(r["open"], r["close"])
                <= max(r["open"], r["close"])
                <= r["high"]
                for r in rows
            )
            audit.check(name + "_prices", "pass" if valid and rows else "blocked")
            for field in ("dividends", "splits"):
                missing = sum(field not in r for r in rows)
                invalid = sum(
                    field in r
                    and (
                        type(r[field]) not in (int, float)
                        or not math.isfinite(r[field])
                        or r[field] < 0
                    )
                    for r in rows
                )
                audit.check(
                    name + "_" + field,
                    "blocked" if invalid else "unknown" if missing else "pass",
                    missing_rows=missing,
                    event_rows=sum(
                        type(r.get(field)) in (int, float) and r[field] > 0 for r in rows
                    ),
                )
            audit.check(
                name + "_action_source_verified",
                "pass" if config.get("action_fields_verified", {}).get(name) is True else "unknown",
            )
        lookback, calibration, horizon = (
            config[k] for k in ("lookback_sessions", "calibration_windows", "horizon_sessions")
        )
        if any(type(v) is not int or v < 1 for v in (lookback, calibration, horizon)):
            raise ValueError("invalid windows")
        exposures = config.get("exposures")
        audit.check("exposure_inventory", "pass" if isinstance(exposures, list) else "unknown")
        intervals = exposures or []
        for interval in intervals:
            iso_date.fromisoformat(interval["start"])
            iso_date.fromisoformat(interval["end"])
            if (
                interval["kind"]
                not in {"development_decision", "development_outcome", "calibration_analysis"}
                or interval["start"] > interval["end"]
            ):
                raise ValueError("invalid exposure")
        candidates = config["candidate_sessions"]
        if not candidates or len(candidates) != len(set(candidates)):
            raise ValueError("invalid candidates")
        eligible = 0
        for n, date in enumerate(candidates):
            if date not in sessions or not schedule_ok:
                audit.check(f"candidate_{n + 1}", "blocked", reasons=["missing_session"])
                continue
            i = sessions.index(date)
            # Calibration exits strictly before decision: j + 1 + H < i.
            reasons = []
            if i < lookback - 1:
                reasons.append("feature_lookback")
            if i - horizon - 1 < calibration:
                reasons.append("calibration_coverage")
            if i + horizon + 1 >= len(sessions):
                reasons.append("outcome_coverage")
            end = sessions[min(i + horizon + 1, len(sessions) - 1)]
            overlap = sum(x["start"] <= end and x["end"] >= date for x in intervals)
            if overlap:
                reasons.append("exposed_decision_or_outcome_window")
            eligible += not reasons
            audit.check(
                f"candidate_{n + 1}",
                "blocked" if reasons else "pass",
                reasons=reasons,
                exposure_overlaps=overlap,
                decision_session=date,
                feature_start=sessions[i - lookback + 1] if i >= lookback - 1 else None,
                calibration_first_decision=sessions[i - horizon - 1 - calibration]
                if i - horizon - 1 >= calibration
                else None,
                calibration_last_exit=sessions[i - 1] if i > 0 else None,
                outcome_entry=sessions[i + 1] if i + 1 < len(sessions) else None,
                outcome_exit=sessions[i + horizon + 1] if i + horizon + 1 < len(sessions) else None,
            )
        audit.check(
            "candidate_coverage",
            "pass" if eligible == len(candidates) else "blocked",
            eligible=eligible,
            excluded=len(candidates) - eligible,
        )
        membership = config.get("membership")
        if config.get("requires_historical_membership") is False:
            membership_status = "not_applicable"
        elif not isinstance(membership, dict):
            membership_status = "blocked"
        else:
            iso_date.fromisoformat(membership["as_of"])
            body = {k: v for k, v in membership.items() if k != "hash"}
            membership_status = (
                "pass"
                if (
                    membership.get("hash") == digest(body)
                    and membership.get("source")
                    and membership.get("as_of", "9999") <= min(candidates)
                    and config.get("symbols", {}).get("asset") in membership.get("members", [])
                )
                else "blocked"
            )
        audit.check("historical_membership_evidence", membership_status)
        audit.check(
            "symbol_mapping",
            "pass"
            if all(config.get("symbols", {}).get(k) for k in ("asset", "benchmark"))
            else "unknown",
        )
        audit.check(
            "protocol_freeze", "pass" if config.get("protocol_frozen") is True else "blocked"
        )
    except (KeyError, TypeError, ValueError, AttributeError):
        audit.check("data_structure", "blocked")
    return audit.result()


def write_report(report: dict[str, Any], output: Path, roots: list[Path]) -> None:
    if any(output.resolve().is_relative_to(root.resolve()) for root in roots):
        raise ValueError("output must be outside inputs")
    output.mkdir(parents=True, exist_ok=False, mode=0o700)
    write_private(output / "verification.json", report)
    lines = [
        "# Inalpha maintainer evidence report",
        "",
        f"Status: {report['status']}",
        "",
        "Formal readiness: not certified.",
        "",
        "| Check | Status |",
        "| --- | --- |",
    ]
    lines += [f"| {c['check']} | {c['status']} |" for c in report["checks"]]
    lines += ["", *report["limitations"], "", "File hashes and counts: verification.json."]
    path = output / "verification.md"
    with path.open("x") as file:
        path.chmod(0o600)
        file.write("\n".join(lines) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    packet = sub.add_parser("packet")
    packet.add_argument("--campaign", type=Path, required=True)
    packet.add_argument("--materials", type=Path, required=True)
    data = sub.add_parser("data")
    data.add_argument("--market", type=Path, required=True)
    data.add_argument("--config", type=Path, required=True)
    for command in (packet, data):
        command.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "packet":
            report = packet_audit(args.campaign, args.materials)
            roots = [args.campaign, args.materials]
        else:
            report = data_audit(
                json.loads(args.market.read_text()), json.loads(args.config.read_text())
            )
            roots = [args.market.parent, args.config.parent]
        write_report(report, args.output, roots)
    except (OSError, ValueError) as exc:
        parser.exit(2, f"Cannot produce report: {type(exc).__name__}\n")
    print(f"{report['status']}; report written; no formal readiness claim")
    raise SystemExit(1 if report["status"] == "blocked" else 0)


if __name__ == "__main__":
    main()
