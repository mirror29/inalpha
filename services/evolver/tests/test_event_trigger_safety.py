"""Snapshot evidence types must not acquire direct-trigger privileges."""

import pytest

from inalpha_evolver.hypothesis.compiler import expand_implementations
from inalpha_evolver.hypothesis.models import HypothesisSpec


@pytest.mark.parametrize(
    "event_type",
    ["regulatory", "upgrade", "unlock", "burn", "partnership", "macro", "other"],
)
def test_confirmed_evidence_cannot_become_a_direct_trigger(event_type: str) -> None:
    """Broader frozen evidence retains the existing confirmation boundary."""
    payload = {
        "lane": "event",
        "thesis": "Frozen evidence requires price and volume confirmation.",
        "event_types": [event_type],
        "assets": ["SOL"],
        "asset_ids": ["asset:SOL"],
        "direction": "long",
    }
    with pytest.raises(ValueError, match="direct trigger is restricted"):
        HypothesisSpec(**payload, trigger_mode="direct")
    spec = HypothesisSpec(**payload, trigger_mode="confirmed")
    arms = expand_implementations(spec)
    assert len(arms) == 3
    assert all(arm.trigger_mode != "direct" for arm in arms)


@pytest.mark.parametrize("event_type", ["listing", "delisting", "exploit", "chain_halt"])
def test_existing_direct_trigger_types_remain_allowed(event_type: str) -> None:
    """The snapshot repair neither expands nor removes direct permissions."""
    spec = HypothesisSpec(
        lane="event",
        thesis="Frozen event evidence supports an explicitly allowed direct trigger.",
        event_types=[event_type],
        assets=["SOL"],
        asset_ids=["asset:SOL"],
        direction="long",
        trigger_mode="direct",
    )
    assert spec.trigger_mode == "direct"
