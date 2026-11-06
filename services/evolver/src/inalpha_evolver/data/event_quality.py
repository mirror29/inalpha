"""Version-aware known-defect guard; never rewrite frozen event evidence."""

from collections import Counter
from typing import Any

from inalpha_paper.model.market_events import MarketEvent

QUALITY_POLICY_VERSION = "legacy-regulatory-ban-provenance-v1"
_LEGACY = {"deterministic-event-extractor-v1", "deterministic-event-extractor-v2"}
_SUPPORTED = _LEGACY | {"deterministic-event-extractor-v3"}


def inspect_event_quality(
    events: list[MarketEvent], facts: list[dict[str, Any]] | None
) -> dict[str, Any]:
    """Flag risky provenance for discovery context/selection without classifying or excluding facts.

    Legacy regulatory facts are potentially affected, not asserted to be the 17
    audited false positives. Nonempty unknown versions require an explicit policy
    review; a version number is never compared lexically to certify quality.
    """
    provenance = {
        str(fact.get("fact_id") or ""): fact.get("extractor_version") for fact in facts or []
    }
    versions: Counter[str] = Counter()
    legacy = 0
    unverified = 0
    for event in events:
        version = provenance.get(event.event_id)
        label = version if isinstance(version, str) and version else "missing"
        versions[label] += 1
        if label not in _SUPPORTED:
            unverified += 1
        if label in _LEGACY and event.event_type == "regulatory":
            legacy += 1
    blockers = []
    if legacy:
        blockers.append("E2_EVENT_QUALITY_REVIEW_REQUIRED")
    if unverified:
        blockers.append("E2_EVENT_PROVENANCE_UNVERIFIED")
    return {
        "policy_version": QUALITY_POLICY_VERSION,
        "status": "review_required" if blockers else "no_known_legacy_defect",
        "scope": "discovery_context_and_selection",
        "inspected_fact_count": len(events),
        "extractor_version_counts": dict(sorted(versions.items())),
        "legacy_regulatory_fact_count": legacy,
        "unverified_provenance_fact_count": unverified,
        "blocker_codes": blockers,
        "semantic_accuracy": "not_evaluated",
        "historical_facts_modified": False,
        "next_action": "review_provenance_and_prepare_new_evidence"
        if blockers
        else "continue_input_checks",
    }
