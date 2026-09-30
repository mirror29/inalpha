"""Preview or extract a bounded set of archived, genuine news events through Data API."""

from __future__ import annotations

import argparse
import asyncio
import json
from collections import Counter
from uuid import UUID

from inalpha_research.config import get_research_settings
from inalpha_research.data_client import DataClient
from inalpha_research.event_extractor import extract_event_fact
from inalpha_research.service_tokens import mint_data_event_token


def parse_args() -> argparse.Namespace:
    """Require explicit archived IDs; preview by default and cap the batch at 100."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw-event-id", type=UUID, action="append", required=True)
    parser.add_argument("--write", action="store_true", help="Persist facts via authenticated Data API")
    args = parser.parse_args()
    args.raw_event_id = list(dict.fromkeys(args.raw_event_id))
    if len(args.raw_event_id) > 100:
        parser.error("at most 100 distinct raw event IDs are allowed")
    return args


async def extract_batch(event_ids: list[UUID], *, write: bool) -> dict[str, object]:
    """Preserve first-seen timestamps, reject fixture provenance, and report coverage."""
    settings = get_research_settings()
    event_types: Counter[str] = Counter()
    assets: Counter[str] = Counter()
    failures: list[dict[str, str]] = []
    processed = 0
    async with DataClient(settings.data_service_url, mint_data_event_token(settings)) as data:
        for event_id in event_ids:
            try:
                raw = await data.get_raw_event(str(event_id))
                if (
                    raw.get("source") not in {"coindesk", "kraken_blog"}
                    or raw.get("collector_version") != "selected-news-forward@1"
                    or raw.get("policy_version") != "first-seen-only-v1"
                    or raw.get("retracted")
                ):
                    raise ValueError("unsupported provenance")
                fact = extract_event_fact(raw, str(raw["policy_version"]))
                if write:
                    await data.write_event_fact(fact)
                processed += 1
                event_types.update([fact["event_type"]])
                assets.update(fact["assets"])
            except Exception as exc:
                # Report types rather than upstream messages, which may contain sensitive data.
                failures.append({"event_id": str(event_id), "error_type": type(exc).__name__})
    return {
        "mode": "write" if write else "preview",
        "requested": len(event_ids),
        "processed": processed,
        "event_types": dict(event_types),
        "assets": dict(assets),
        "failures": failures,
    }


def main() -> None:
    """Print aggregate diagnostics; writes are idempotent and never call an LLM."""
    args = parse_args()
    result = asyncio.run(extract_batch(args.raw_event_id, write=args.write))
    print(json.dumps(result, ensure_ascii=False))
    if result["failures"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
