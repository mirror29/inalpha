"""Known legacy classifier risks must block paid research without changing counts."""

import pytest

from inalpha_evolver.data.event_coverage import inspect_event_coverage

from .test_event_coverage import dataset, event


def facts_for(events, version="deterministic-event-extractor-v3"):
    return [{"fact_id": value.event_id, "extractor_version": version} for value in events]


def test_legacy_regulatory_blocks_even_with_eight_independent_events():
    data = dataset()
    events = [event(data, 730 + 24 * i, "regulatory") for i in range(8)]
    result = inspect_event_coverage(
        data, events, "BTC", facts=facts_for(events, "deterministic-event-extractor-v2")
    )
    assert result["independent_event_upper_bound"] == 8
    assert result["selection_fact_count"] == 8
    assert result["status"] == "blocked"
    assert result["blocker_codes"] == ["E2_EVENT_QUALITY_REVIEW_REQUIRED"]
    assert result["event_quality"]["legacy_regulatory_fact_count"] == 8
    assert result["event_quality"]["policy_version"] == "legacy-regulatory-ban-provenance-v1"
    assert result["matched_controls"] == result["fdr"] == "not_evaluated"


def test_quality_guard_covers_discovery_but_not_foreign_selection_or_holdout_facts():
    data = dataset()
    events = [event(data, 730 + 24 * i) for i in range(8)]
    risk = event(data, 100, "regulatory")
    extras = [event(data, 980, "regulatory"), event(data, 800, "regulatory", "SOL")]
    result = inspect_event_coverage(
        data,
        [*events, risk, *extras],
        "BTC",
        facts=facts_for(events) + facts_for([risk, *extras], "deterministic-event-extractor-v2"),
    )
    assert result["event_quality"]["legacy_regulatory_fact_count"] == 1
    assert result["event_quality"]["inspected_fact_count"] == 9
    assert result["status"] == "blocked"


def test_corrected_version_is_not_claimed_semantically_certified():
    data = dataset()
    events = [event(data, 730 + 24 * i, "regulatory") for i in range(8)]
    result = inspect_event_coverage(data, events, "BTC", facts=facts_for(events))
    assert result["status"] == "necessary_inputs_present"
    assert result["event_quality"]["status"] == "no_known_legacy_defect"
    assert result["event_quality"]["semantic_accuracy"] == "not_evaluated"


def test_missing_or_unknown_provenance_never_silently_qualifies():
    data = dataset()
    events = [event(data, 730 + 24 * i) for i in range(8)]
    for facts in ([], facts_for(events, "unknown-extractor")):
        result = inspect_event_coverage(data, events, "BTC", facts=facts)
        assert result["status"] == "blocked"
        assert "E2_EVENT_PROVENANCE_UNVERIFIED" in result["blocker_codes"]


def test_insufficient_coverage_and_legacy_quality_are_both_explained():
    data = dataset()
    events = [event(data, 730, "regulatory")]
    result = inspect_event_coverage(
        data, events, "BTC", facts=facts_for(events, "deterministic-event-extractor-v1")
    )
    assert result["blocker_codes"] == [
        "E2_INPUT_COVERAGE_INSUFFICIENT",
        "E2_EVENT_QUALITY_REVIEW_REQUIRED",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("legacy,count", [(True, 8), (False, 1), (True, 1)])
async def test_campaign_stops_before_credential_access_for_blocked_inputs(monkeypatch, legacy, count):
    from datetime import UTC, datetime
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from inalpha_shared.errors import ValidationError

    from inalpha_evolver.runtime import campaign

    data = dataset()
    events = [event(data, 730 + 24 * i, "regulatory") for i in range(count)]
    facts = [
        {
            "fact_id": value.event_id,
            "event_type": value.event_type,
            "assets": ["BTC"],
            "effective_at": datetime.fromtimestamp(value.effective_at / 1e9, UTC).isoformat(),
            "available_at": datetime.fromtimestamp(value.available_at / 1e9, UTC).isoformat(),
            "extractor_version": "deterministic-event-extractor-v2"
            if legacy
            else "deterministic-event-extractor-v3",
        }
        for value in events
    ]
    credentials = AsyncMock()
    scope = AsyncMock()
    monkeypatch.setattr(
        campaign, "_campaign_config", lambda _: SimpleNamespace(event_asset_code="BTC")
    )
    monkeypatch.setattr(
        campaign, "_load_frozen_inputs", AsyncMock(return_value=(data, {"facts": facts}))
    )
    monkeypatch.setattr(campaign, "build_owner_mutator", credentials)
    monkeypatch.setattr(campaign, "campaign_model_scope", scope)
    with pytest.raises(ValidationError) as raised:
        await campaign.execute_campaign({"status": "running"}, SimpleNamespace())
    assert raised.value.code == (
        "E2_EVENT_QUALITY_REVIEW_REQUIRED" if legacy and count == 8 else "E2_INPUT_COVERAGE_INSUFFICIENT"
    )
    if legacy:
        assert "E2_EVENT_QUALITY_REVIEW_REQUIRED" in raised.value.message
    if count < 8:
        assert "E2_INPUT_COVERAGE_INSUFFICIENT" in raised.value.message
    credentials.assert_not_awaited()
    scope.assert_not_awaited()


def test_moving_window_cannot_hide_older_facts_still_sent_to_proposer():
    from dataclasses import replace

    data = dataset()
    events = [event(data, 730 + 24 * i) for i in range(8)]
    older = replace(
        event(data, 100, "regulatory", "SOL"), available_at=data.bars[0].bar_known_at - 3600 * 10**9
    )
    result = inspect_event_coverage(
        data,
        [*events, older],
        "BTC",
        facts=[*facts_for(events), *facts_for([older], "deterministic-event-extractor-v2")],
    )
    assert result["status"] == "blocked"
    assert result["event_quality"]["legacy_regulatory_fact_count"] == 1
    assert result["independent_event_upper_bound"] == 8


def test_omitted_facts_fail_closed_for_sufficient_events():
    data = dataset()
    events = [event(data, 730 + 24 * i) for i in range(8)]
    result = inspect_event_coverage(data, events, "BTC")
    assert result["status"] == "blocked"
    assert result["blocker_codes"] == ["E2_EVENT_PROVENANCE_UNVERIFIED"]
