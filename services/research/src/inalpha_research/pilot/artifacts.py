"""Versioned local experiment artifacts. Future outcomes are a separate input type."""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

VERSION = "pilot-v1"
_SECRET_KEY = re.compile(
    r"^(api[_-]?key|authorization|access[_-]?token|refresh[_-]?token|jwt|password|secret|llm_credential_grant)$",
    re.I,
)
_SECRET_TEXT = re.compile(r"Bearer\s+[A-Za-z0-9._~-]{16,}|\bsk-[A-Za-z0-9_-]{16,}")


def safe_json(value: Any) -> bytes:
    """Reject credential-shaped fields and common bearer/key literals before persistence."""

    def inspect(item: Any) -> None:
        if isinstance(item, dict):
            for key, child in item.items():
                if _SECRET_KEY.match(str(key)):
                    raise ValueError("credential field is forbidden in artifacts")
                inspect(child)
        elif isinstance(item, list):
            for child in item:
                inspect(child)
        elif isinstance(item, str) and _SECRET_TEXT.search(item):
            raise ValueError("credential-like content is forbidden in artifacts")

    inspect(value)
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()


def digest(value: Any) -> str:
    return hashlib.sha256(safe_json(value)).hexdigest()


def instant(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timezone is required")
    return parsed


def freeze(case_id: str, decision_at: str, evidence: list[dict[str, Any]]) -> dict[str, Any]:
    """Freeze exact inputs; unknown availability is retained but blocks real evaluation."""
    instant(decision_at)
    items = []
    for item in evidence:
        if set(item) - {
            "id",
            "kind",
            "content",
            "source",
            "available_at",
            "uncertainty",
            "session_close",
        }:
            raise ValueError("unexpected evidence fields (outcomes must remain separate)")
        if item.get("kind") not in ("price", "news", "fundamental", "annotation"):
            raise ValueError("unsupported evidence kind")
        if not all(item.get(k) for k in ("id", "content", "source")):
            raise ValueError("evidence requires identity, content and provenance")
        if item.get("available_at") is None and not item.get("uncertainty"):
            raise ValueError("unknown availability requires explicit uncertainty")
        items.append({**item, "hash": digest(item)})
    bundle = {"version": VERSION, "case_id": case_id, "decision_at": decision_at, "evidence": items}
    return {**bundle, "hash": digest(bundle)}


def validate_bundle(bundle: dict[str, Any]) -> list[str]:
    """Return readiness blockers; never substitute newer evidence or infer availability."""
    problems = []
    if (
        set(bundle) != {"version", "case_id", "decision_at", "evidence", "hash"}
        or bundle.get("version") != VERSION
    ):
        return ["invalid bundle schema"]
    if digest({k: v for k, v in bundle.items() if k != "hash"}) != bundle["hash"]:
        problems.append("bundle hash mismatch")
    try:
        freeze(
            bundle["case_id"],
            bundle["decision_at"],
            [{k: v for k, v in item.items() if k != "hash"} for item in bundle["evidence"]],
        )
    except (ValueError, KeyError, TypeError):
        return [*problems, "invalid evidence schema"]
    decision = instant(bundle["decision_at"])
    seen = set()
    if not bundle["evidence"]:
        problems.append("empty evidence")
    for item in bundle["evidence"]:
        if item["id"] in seen:
            problems.append("duplicate evidence identity")
        seen.add(item["id"])
        body = {k: v for k, v in item.items() if k != "hash"}
        if digest(body) != item["hash"]:
            problems.append("evidence hash mismatch")
        if not item.get("available_at"):
            problems.append("unknown availability")
        elif instant(item["available_at"]) > decision:
            problems.append("future evidence")
        if item["kind"] == "price":
            if not item.get("session_close"):
                problems.append("price requires explicit session close")
            elif instant(item["session_close"]) > decision:
                problems.append("unfinished session")
            elif item.get("available_at") and instant(item["available_at"]) < instant(
                item["session_close"]
            ):
                problems.append("price availability precedes session close")
    return problems


def validate_materials(materials: dict[str, Any]) -> list[str]:
    """Validate agreed conventions and imported labels without selecting their semantics."""
    problems = []
    protocol = materials.get("protocol", {})
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
    ):
        if key not in protocol or protocol[key] is None:
            problems.append(f"protocol missing {key}")
    dev, formal = materials.get("development", []), materials.get("formal", [])
    for field in ("case_id", "decision_at"):
        if {c[field] for c in dev} & {c[field] for c in formal}:
            problems.append(f"development/formal overlap: {field}")
    for case in dev + formal:
        problems.extend(validate_bundle(case))
    bars = materials.get("market", {})
    asset, benchmark = bars.get("asset", []), bars.get("benchmark", [])
    schedule = bars.get("sessions", [])
    if not schedule:
        problems.append("missing agreed session schedule")
    for label, rows in (("asset", asset), ("benchmark", benchmark)):
        sessions = [row.get("session") for row in rows]
        if len(set(sessions)) != len(sessions):
            problems.append(f"duplicate {label} bars")
        if sessions != schedule:
            problems.append(f"{label} sessions do not match calendar")
        for row in rows:
            if (
                not row.get("source")
                or not row.get("raw")
                or row.get("hash") != digest(row.get("raw"))
            ):
                problems.append(f"{label} missing provenance or hash mismatch")
    cases = {case["case_id"]: case for case in dev + formal}
    if formal and protocol.get("frozen") is not True:
        problems.append("formal protocol is not frozen")
    if len(schedule) != len(set(schedule)):
        problems.append("duplicate expected sessions")
    for kind in ("labels", "annotations"):
        rows = materials.get(kind, [])
        if not rows:
            problems.append(f"missing {kind}")
        covered = [row.get("case_id") for row in rows]
        if set(covered) != set(cases):
            problems.append(f"{kind} case coverage mismatch")
        if len(covered) != len(set(covered)):
            problems.append(f"duplicate {kind} case")
        for row in rows:
            required = (
                ("case_id", "protocol_version", "provenance", "code_version", "value")
                if kind == "labels"
                else (
                    "case_id",
                    "protocol_version",
                    "provenance",
                    "rubric_version",
                    "blinded",
                    "criteria",
                )
            )
            if any(row.get(key) in (None, "") for key in required) or row.get(
                "protocol_version"
            ) != protocol.get("version"):
                problems.append(f"invalid {kind} provenance")
            if kind == "annotations":
                evidence_ids = row.get("evidence_ids")
                allowed = {
                    item["id"] for item in cases.get(row.get("case_id"), {}).get("evidence", [])
                }
                if not isinstance(evidence_ids, list) or not set(evidence_ids) <= allowed:
                    problems.append("annotation evidence not in frozen case")
            if kind == "annotations" and row.get("blinded") is not True:
                problems.append("annotations are not blinded")
    return sorted(set(problems))


class PilotArtifacts:
    """Exclusive run directory and append-only events retain partial and interrupted runs."""

    def __init__(
        self,
        directory: Path,
        *,
        experiment_id: str,
        case: dict[str, Any],
        arm_id: str,
        configuration: dict[str, Any],
    ):
        blockers = validate_bundle(case)
        if blockers:
            raise ValueError("; ".join(blockers))
        self.run_id = str(uuid4())
        self.path = directory / self.run_id
        self.path.mkdir(parents=True, exist_ok=False, mode=0o700)
        self.links = dict(
            experiment_id=experiment_id, case_id=case["case_id"], arm_id=arm_id, run_id=self.run_id
        )
        self.configuration = configuration
        self.write(
            "manifest.json",
            {
                "version": VERSION,
                **self.links,
                "evidence_hash": case["hash"],
                "configuration": configuration,
                "configuration_hash": digest(configuration),
                "model_metadata": {
                    "provider": configuration.get("provider"),
                    "requested_model": configuration.get("model"),
                    "immutable_revision": configuration.get("immutable_revision"),
                    "training_cutoff": configuration.get("training_cutoff"),
                    "cutoff_evidence": configuration.get("cutoff_evidence"),
                },
            },
        )
        self.write("evidence.json", case)
        self.case = case

    def write(self, name: str, value: Any) -> None:
        if Path(name).name != name:
            raise ValueError("artifact filename must not contain a path")
        payload = safe_json(value)
        with os.fdopen(
            os.open(self.path / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb"
        ) as file:
            file.write(payload)
            file.flush()
            os.fsync(file.fileno())

    def event(self, value: dict[str, Any]) -> None:
        payload = safe_json(value) + b"\n"
        with os.fdopen(
            os.open(self.path / "calls.jsonl", os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600), "ab"
        ) as file:
            file.write(payload)
            file.flush()
            os.fsync(file.fileno())

    def model_input(self) -> str:
        """The model receives evidence only; this object has no network/data client."""
        return safe_json(self.case).decode()

    def finish(self, output: Any, status: str = "completed") -> None:
        if status not in {"completed", "failed", "invalid", "truncated", "interrupted"}:
            raise ValueError("invalid run status")
        self.write("result.json", {"status": status, "output": output})


def inventory(paths: list[Path]) -> list[dict[str, Any]]:
    """Return field coverage and content hashes only, never conversation values."""
    report = []
    for path in paths:
        raw = path.read_bytes()
        value = json.loads(raw)
        rows = value if isinstance(value, list) else [value]
        fields = sorted({str(key) for row in rows if isinstance(row, dict) for key in row})
        report.append(
            {
                "source": path.name,
                "sha256": hashlib.sha256(raw).hexdigest(),
                "records": len(rows),
                "fields": fields,
                "complete_research_records": sum(
                    isinstance(row, dict)
                    and all(k in row for k in ("briefs", "debate_log", "thesis"))
                    for row in rows
                ),
            }
        )
    return report


def validate_run(directory: Path) -> list[str]:
    """Check traceability without treating a partial run as successful or bill-free."""
    problems = []
    manifest = json.loads((directory / "manifest.json").read_text())
    case = json.loads((directory / "evidence.json").read_text())
    problems.extend(validate_bundle(case))
    if manifest.get("evidence_hash") != case.get("hash"):
        problems.append("manifest evidence mismatch")
    if manifest.get("configuration_hash") != digest(manifest.get("configuration")):
        problems.append("configuration hash mismatch")
    for key in ("experiment_id", "case_id", "arm_id", "run_id"):
        if not manifest.get(key):
            problems.append(f"missing {key}")
    events_path = directory / "calls.jsonl"
    events = (
        [json.loads(line) for line in events_path.read_text().splitlines()]
        if events_path.exists()
        else []
    )
    starts = [event.get("call_id") for event in events if event.get("event") == "started"]
    ends = [event.get("call_id") for event in events if event.get("event") == "settled"]
    if not starts:
        problems.append("no recorded calls")
    if len(starts) != len(set(starts)) or len(ends) != len(set(ends)):
        problems.append("duplicate call events")
    if set(starts) != set(ends):
        problems.append("incomplete call events")
    if any(
        event.get("ledger_settled") is not True
        for event in events
        if event.get("event") == "settled"
    ):
        problems.append("ledger settlement unconfirmed")
    result_path = directory / "result.json"
    if not result_path.exists():
        problems.append("run has no terminal result")
    elif json.loads(result_path.read_text()).get("status") != "completed":
        problems.append("run did not complete successfully")
    return sorted(set(problems))
