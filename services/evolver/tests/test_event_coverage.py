"""Necessary input checks must never turn a nonempty snapshot into a pass claim."""

from dataclasses import replace
from datetime import UTC, datetime

import pytest
from inalpha_paper.model.market_events import MarketEvent

from inalpha_evolver.data.event_coverage import inspect_event_coverage
from inalpha_evolver.data.manifest import FrozenDataset

from .test_persistent_snapshot import _dataset


def dataset():
    original = _dataset()
    first = original.bars[0]
    hour = 3600 * 10**9
    bars = tuple(
        replace(
            first,
            ts_open=first.ts_open + i * hour,
            ts_event=first.ts_event + i * hour,
            ts_init=first.ts_init + i * hour,
        )
        for i in range(1200)
    )
    return FrozenDataset(
        bars,
        original.manifest.model_copy(
            update={
                "bar_count": len(bars),
                "requested_as_of": datetime.fromtimestamp(bars[-1].bar_known_at / 1e9, UTC),
            }
        ),
    )


def event(data, index, kind="other", asset="BTC"):
    instant = data.bars[index].bar_known_at
    return MarketEvent(str(index) + kind, kind, (asset,), "", 1, 1, instant, instant)


def test_nonempty_snapshot_outside_selection_is_blocked():
    data = dataset()
    result = inspect_event_coverage(data, [event(data, 100), event(data, 1100)], "BTC")
    assert result["status"] == "blocked"
    assert result["selection_fact_count"] == 0
    assert result["blocker_codes"] == ["E2_INPUT_COVERAGE_INSUFFICIENT"]


def test_versions_same_type_within_day_do_not_fill_eight_pairs():
    data = dataset()
    result = inspect_event_coverage(data, [event(data, i) for i in range(730, 750)], "BTC")
    assert result["selection_fact_count"] == 20
    assert result["independent_event_upper_bound"] == 1
    assert result["status"] == "blocked"


def test_other_supported_and_exact_day_boundary_count_without_pass_claim():
    data = dataset()
    result = inspect_event_coverage(data, [event(data, 730 + 24 * i) for i in range(8)], "BTC")
    assert result["independent_event_upper_bound"] == 8
    assert result["status"] == "necessary_inputs_present"
    assert result["matched_controls"] == "not_evaluated"
    assert result["fdr"] == "not_evaluated"


def test_foreign_asset_and_holdout_do_not_supply_selection_evidence():
    data = dataset()
    events = [event(data, 730 + 24 * i, asset="SOL") for i in range(8)]
    events += [event(data, 1000 + i) for i in range(10)]
    assert inspect_event_coverage(data, events, "BTC")["selection_fact_count"] == 0


def test_types_have_separate_independence_clusters():
    data = dataset()
    result = inspect_event_coverage(
        data, [event(data, 730, "upgrade"), event(data, 731, "other")], "BTC"
    )
    assert result["independent_event_upper_bound"] == 2


def test_selection_edges_are_permissive_upper_bound_not_execution_claim():
    data = dataset()
    result = inspect_event_coverage(data, [event(data, i) for i in (719, 720, 959, 960)], "BTC")
    assert result["selection_fact_count"] == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("snapshot_unavailable", [False, True])
async def test_worker_blocks_before_model_execution(monkeypatch, snapshot_unavailable):
    from contextlib import asynccontextmanager
    from unittest.mock import AsyncMock
    from uuid import uuid4

    import pytest
    from inalpha_shared.errors import ValidationError

    from inalpha_evolver.config import EvolverSettings
    from inalpha_evolver.runtime import loop_baseline

    from .test_loop_start import make_request

    body = make_request(uuid4())
    loop = {key: uuid4() for key in ("loop_id", "owner_account_id", "e1_run_id", "lease_token")}
    loop["frozen_config"] = {"campaign_request": body.campaign.model_dump(mode="json")}
    run = {"status": "queued"}

    @asynccontextmanager
    async def connection():
        yield object()

    fact = {
        "fact_id": "one",
        "event_type": "upgrade",
        "assets": ["BTC"],
        "available_at": "2026-01-01T00:00:00Z",
        "effective_at": "2026-01-01T00:00:00Z",
    }
    snapshot = {
        "snapshot_id": str(body.campaign.event_snapshot_id),
        "fact_count": 1,
        "cutoff": body.campaign.config.as_of.isoformat(),
        "facts": [fact],
    }
    monkeypatch.setattr(loop_baseline, "get_conn", connection)
    monkeypatch.setattr(loop_baseline.runs, "get_run", AsyncMock(return_value=run))
    monkeypatch.setattr(loop_baseline.runs, "transition", AsyncMock(return_value=run))
    monkeypatch.setattr(
        loop_baseline, "get_loop_data_snapshot", AsyncMock(return_value={"frozen": True})
    )
    monkeypatch.setattr(loop_baseline, "decode_frozen_dataset", lambda _: dataset())
    monkeypatch.setattr(
        loop_baseline,
        "fetch_event_snapshot",
        AsyncMock(
            return_value=snapshot,
            side_effect=ValidationError("unavailable", code="EVENT_SNAPSHOT_UNAVAILABLE")
            if snapshot_unavailable
            else None,
        ),
    )
    monkeypatch.setattr(loop_baseline, "discovery_dataset", lambda _: dataset())
    monkeypatch.setattr(loop_baseline.loop_dispatch, "complete_step", AsyncMock(return_value=True))
    execute = AsyncMock()
    monkeypatch.setattr(loop_baseline, "execute_frozen_run", execute)
    with pytest.raises(ValidationError) as raised:
        await loop_baseline.execute_loop_baseline(loop, EvolverSettings())
    assert raised.value.code == (
        "EVENT_SNAPSHOT_UNAVAILABLE" if snapshot_unavailable else "E2_INPUT_COVERAGE_INSUFFICIENT"
    )
    if not snapshot_unavailable:
        assert raised.value.details["selection_fact_count"] == 0
    execute.assert_not_awaited()
