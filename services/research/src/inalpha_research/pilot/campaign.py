"""Execute a private four-arm development campaign against the unified Postgres ledger."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from decimal import Decimal
from pathlib import Path
from typing import Any

from inalpha_shared.usage import PostgresUsageStore

from .artifacts import PilotArtifacts, digest, safe_json, validate_bundle, validate_run
from .budget import budget_diagnostics
from .experiment import DEFAULT_TEMPERATURE, AttemptRunner, CampaignBudget, recorder_for, run_arm
from .materials import write_private
from .report import build_report, intervention_plans
from .rescore import arm_tokens


async def campaign(
    inputs: dict[str, Any],
    directory: Path,
    configuration: dict[str, Any],
    store: Any,
    owner: str,
    invoke: Any,
    *,
    intervention_cases: int = 1,
) -> dict[str, Any]:
    """Run sequentially with shared spend reservations and no automatic paid resume."""
    configuration = {**configuration, "tau": inputs["protocol"]["tau"]}
    cases = inputs["cases"]
    if not cases or len({c["case_id"] for c in cases}) != len(cases):
        raise ValueError("unique nonempty development cases required")
    for case in cases:
        errors = validate_bundle(case)
        if errors:
            raise ValueError(str(errors))
    pricing = configuration["pricing"]
    budget = CampaignBudget(
        Decimal(str(configuration["limit_usd"])),
        Decimal(pricing["input_usd_per_million"]),
        Decimal(pricing["output_usd_per_million"]),
    )
    await asyncio.to_thread(directory.mkdir, parents=True, exist_ok=False, mode=0o700)
    experiment_id = directory.name
    write_private(
        directory / "campaign.json",
        {
            "experiment_id": experiment_id,
            "configuration": configuration,
            "inputs_hash": digest(inputs),
            "outcomes_hash": inputs.get("outcomes_hash"),
            "protocol": inputs["protocol"],
        },
    )
    runs, replays = [], []

    async def execute(
        case: dict[str, Any], arm: str, replay: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        artifacts = PilotArtifacts(
            directory,
            experiment_id=experiment_id,
            case=case,
            arm_id=arm,
            configuration={**configuration, "replay": replay},
        )
        if replay:
            artifacts.links["replay_id"] = artifacts.run_id
            artifacts.links["base_run_id"] = replay["base_run_id"]
        runner = AttemptRunner(
            artifacts,
            recorder_for(artifacts, store, owner, pricing),
            invoke,
            budget,
            int(configuration["baseline_tokens"] if arm == "A" else configuration["arm_tokens"]),
        )
        result = await run_arm(
            runner,
            arm,
            units=int(
                configuration.get(
                    "samples" if arm == "B" else "revisions", configuration.get("units", 2)
                )
            ),
            rounds=configuration["rounds"],
            replay=replay,
            sample_temperature=float(configuration.get("sample_temperature", DEFAULT_TEMPERATURE)),
        )
        result["artifact_validation"] = validate_run(artifacts.path)
        return result

    for case in cases:
        for arm in "ABCD":
            runs.append(await execute(case, arm))
    for case in cases[:intervention_cases]:
        base = next(r for r in runs if r["case_id"] == case["case_id"] and r["arm"] == "D")
        for plan in intervention_plans(base, case):
            if "skip" in plan:
                replays.append({"case_id": case["case_id"], **plan})
                continue
            replay = await execute(case, "D", {**plan, "base_run_id": base["run_id"]})
            if plan.get("remove_evidence"):
                removed = next(e for e in case["evidence"] if e["id"] == plan["remove_evidence"])
                text = safe_json([c.get("output") for c in replay["calls"]]).decode()
                numbers = [
                    str(v) for v in removed["content"].values() if isinstance(v, (int, float))
                ]
                replay["reentry"] = {
                    "exact_cited_evidence_id": any(
                        removed["id"] in (c.get("output") or {}).get("cited_evidence_ids", [])
                        for c in replay["calls"]
                    ),
                    "text_id_substring_match": removed["id"] in text,
                    "numeric_literal_substring_match": any(number in text for number in numbers),
                    "method": "exact citation plus substring heuristics; substring matches may be false positives, and inferred or rounded reconstruction may escape detection",
                }
            replays.append(replay)
    result = {
        "runs": runs,
        "replays": replays,
        "estimated_cost_or_reserved_usd": str(budget.spent),
        "stopped_on_unknown_or_accounting": budget.stopped,
        "inputs_hash": digest(inputs),
        "outcomes_hash": inputs.get("outcomes_hash"),
    }
    write_private(directory / "results.json", result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run")
    run.add_argument("inputs", type=Path)
    run.add_argument("directory", type=Path)
    run.add_argument("--config", type=Path, required=True)
    run.add_argument(
        "--owner", required=True, help="Authorized ledger owner for this local/operator run"
    )
    run.add_argument("--intervention-cases", type=int, default=1)
    report = sub.add_parser("report")
    report.add_argument("directory", type=Path)
    report.add_argument("outcomes", type=Path)
    args = parser.parse_args()
    if args.command == "report":
        result = json.loads((args.directory / "results.json").read_text())
        outcomes = json.loads(args.outcomes.read_text())
        if not result.get("outcomes_hash") or digest(outcomes) != result["outcomes_hash"]:
            parser.error("outcome hash differs from the pre-run input commitment")
        setup = json.loads((args.directory / "campaign.json").read_text())
        protocol, configuration = setup["protocol"], setup.get("configuration", {})
        output = build_report(
            result["runs"],
            outcomes,
            result["replays"],
            climatology=protocol.get("climatology"),
        )
        declared = configuration.get("matching_target_tokens")
        output["budget_diagnostics"] = budget_diagnostics(
            result["runs"],
            caps=arm_tokens(configuration, "caps", None),
            targets=arm_tokens(configuration, "targets", declared) if declared else {},
        )
        output["estimated_cost_or_reserved_usd"] = result["estimated_cost_or_reserved_usd"]
        write_private(args.directory / "report.json", output)
        print(json.dumps(output, indent=2))
        return
    config = json.loads(args.config.read_text())
    if config["provider"] != "deepseek" or not config["model"]:
        parser.error("this development CLI requires an explicitly pinned DeepSeek model")
    if not 0 <= args.intervention_cases <= 20:
        parser.error("intervention cases must be between 0 and 20")
    # Deliberately no default credentials, owner or price inference.
    from openai import AsyncOpenAI

    async def execute() -> None:
        async with AsyncOpenAI(
            api_key=os.environ["DEEPSEEK_API_KEY"],
            base_url="https://api.deepseek.com/v1",
            max_retries=0,
            timeout=90,
        ) as client:

            async def invoke(
                *,
                system: str,
                user: str,
                max_tokens: int,
                temperature: float = DEFAULT_TEMPERATURE,
            ) -> Any:
                options: dict[str, Any] = (
                    {"extra_body": {"thinking": {"type": "disabled"}}}
                    if config["model"] == "deepseek-flash"
                    else {}
                )
                return await client.chat.completions.create(
                    model=config["model"],
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    response_format={"type": "json_object"},
                    temperature=temperature,
                    max_tokens=max_tokens,
                    **options,
                )

            result = await campaign(
                json.loads(args.inputs.read_text()),
                args.directory,
                config,
                PostgresUsageStore(os.environ["DATABASE_URL"]),
                args.owner,
                invoke,
                intervention_cases=args.intervention_cases,
            )
            print(
                json.dumps(
                    {
                        "directory": str(args.directory),
                        "runs": len(result["runs"]),
                        "completed": sum(r["status"] == "completed" for r in result["runs"]),
                        "estimated_cost_or_reserved_usd": result["estimated_cost_or_reserved_usd"],
                    }
                )
            )

    asyncio.run(execute())


if __name__ == "__main__":
    main()
