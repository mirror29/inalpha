"""Offline rescoring of a saved campaign into a separate, versioned report.

Never contacts a provider or database and never writes inside the campaign directory: the
original results, report and run artifacts are hashed before and after, and the rescore
fails if any of them changed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .artifacts import digest
from .budget import budget_diagnostics
from .materials import write_private
from .report import build_report

VERSION = "pilot-rescore-v1"


def hash_tree(root: Path) -> list[dict[str, str]]:
    """Content hashes of every regular file under ``root``; symlinks are rejected."""
    files = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ValueError("symlinks are not accepted in campaign inputs")
        if path.is_file():
            files.append(
                {
                    "path": path.relative_to(root).as_posix(),
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                }
            )
    return files


def arm_tokens(configuration: dict[str, Any], key: str, value: Any) -> dict[str, int | None]:
    """Expand an int (B/C/D; A from its own field) or an explicit per-arm mapping."""
    if isinstance(value, dict):
        return {arm: value.get(arm) for arm in "ABCD"}
    if key == "caps":
        return {
            "A": configuration.get("baseline_tokens"),
            **dict.fromkeys("BCD", configuration.get("arm_tokens")),
        }
    return {"A": None, **dict.fromkeys("BCD", value)}


def rescore(
    campaign: Path,
    outcomes_path: Path,
    *,
    targets: dict[str, int | None] | None = None,
    code_revision: str | None = None,
) -> dict[str, Any]:
    """Recompute scores and budget diagnostics from saved outputs with a provenance envelope."""
    before = hash_tree(campaign)
    outcomes_raw = outcomes_path.read_bytes()
    setup = json.loads((campaign / "campaign.json").read_text())
    result = json.loads((campaign / "results.json").read_text())
    outcomes = json.loads(outcomes_raw)
    if not result.get("outcomes_hash") or digest(outcomes) != result["outcomes_hash"]:
        raise ValueError("outcome hash differs from the pre-run input commitment")
    configuration = setup.get("configuration", {})
    declared = configuration.get("matching_target_tokens")
    if targets is None:
        targets = arm_tokens(configuration, "targets", declared) if declared else {}
        target_source = "campaign configuration" if declared else "not declared"
    else:
        target_source = "rescoring parameter"
    caps = arm_tokens(configuration, "caps", None)
    climatology = setup.get("protocol", {}).get("climatology")
    report = build_report(
        result["runs"],
        outcomes,
        result["replays"],
        climatology=climatology,
        selection=result.get("intervention_selection"),
    )
    budgets = budget_diagnostics(result["runs"], caps=caps, targets=targets)
    if hash_tree(campaign) != before:
        raise ValueError("campaign inputs changed during rescoring")
    original = next((f for f in before if f["path"] == "report.json"), None)
    return {
        "version": VERSION,
        "generated_at": datetime.now(UTC).isoformat(),
        "code_revision": code_revision,
        "scope": "offline rescoring of saved outputs; no provider calls; original files untouched",
        "sources": {
            "campaign_files": before,
            "outcomes_sha256": hashlib.sha256(outcomes_raw).hexdigest(),
            "original_report_sha256": original["sha256"] if original else None,
            "inputs_hash": result.get("inputs_hash"),
            "outcomes_hash": result.get("outcomes_hash"),
        },
        "checks": {
            "outcome_commitment_verified": True,
            "campaign_files_unchanged": True,
        },
        "parameters": {
            "matching_target_tokens": targets,
            "matching_target_source": target_source,
            "safety_cap_tokens": caps,
            "climatology": climatology,
            "climatology_source": "campaign protocol" if climatology else "absent",
        },
        "report": report,
        "budget_diagnostics": budgets,
        "limitations": [
            "rescoring cannot validate settings or stimuli that the saved run did not use",
            "unknown provider usage stays excluded from known totals",
        ],
    }


def write_rescore(value: dict[str, Any], output: Path, roots: list[Path]) -> None:
    if any(output.resolve().is_relative_to(root.resolve()) for root in roots):
        raise ValueError("output must be outside the campaign directory")
    output.mkdir(parents=True, exist_ok=False, mode=0o700)
    write_private(output / "rescore.json", value)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("campaign", type=Path)
    parser.add_argument("outcomes", type=Path)
    parser.add_argument("output", type=Path, help="new directory outside the campaign")
    parser.add_argument("--target-tokens", type=int, help="matching target T for B/C/D")
    parser.add_argument("--target-tokens-a", type=int, help="optional target for arm A")
    parser.add_argument("--code-revision", help="scoring code revision recorded in the report")
    args = parser.parse_args()
    targets = None
    if args.target_tokens is not None or args.target_tokens_a is not None:
        if any(t is not None and t <= 0 for t in (args.target_tokens, args.target_tokens_a)):
            parser.error("targets must be positive")
        targets = {"A": args.target_tokens_a, **dict.fromkeys("BCD", args.target_tokens)}
    try:
        value = rescore(
            args.campaign, args.outcomes, targets=targets, code_revision=args.code_revision
        )
        write_rescore(value, args.output, [args.campaign])
    except (OSError, ValueError) as exc:
        parser.exit(2, f"Cannot rescore: {exc}\n")
    print(json.dumps({"output": str(args.output / "rescore.json"), "version": VERSION}))


if __name__ == "__main__":
    main()
