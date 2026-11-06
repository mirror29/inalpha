"""Archived-event verification must reject fixture provenance and preview without writes."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from uuid import UUID

import pytest

spec = importlib.util.spec_from_file_location(
    "archived_event_cli", Path(__file__).parents[1] / "scripts/extract_archived_events.py"
)
assert spec and spec.loader
cli = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cli)


@pytest.mark.asyncio
@pytest.mark.parametrize("fixture,write", [(False, False), (False, True), (True, True)])
async def test_provenance_and_preview(monkeypatch, fixture: bool, write: bool) -> None:
    calls = []
    raw = {
        "source": "coindesk",
        "collector_version": "test@1" if fixture else "selected-news-forward@1",
        "policy_version": "first-seen-only-v1",
        "retracted": False,
    }

    class FakeData:
        def __init__(self, *_args):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            pass

        async def get_raw_event(self, event_id):
            calls.append(("read", event_id))
            return raw

        async def write_event_fact(self, fact):
            calls.append(("write", fact))

    class Settings:
        data_service_url = "http://unused"

    def extract(record, policy):
        assert record is raw and policy == "first-seen-only-v1"
        return {"event_type": "listing", "assets": ["BTC"]}

    monkeypatch.setattr(cli, "DataClient", FakeData)
    monkeypatch.setattr(cli, "get_research_settings", Settings)
    monkeypatch.setattr(cli, "mint_data_event_token", lambda _: "test-token")
    monkeypatch.setattr(cli, "extract_event_fact", extract)
    result = await cli.extract_batch([UUID("11111111-1111-4111-8111-111111111111")], write=write)
    assert result["processed"] == (0 if fixture else 1)
    assert bool(result["failures"]) == fixture
    assert len([call for call in calls if call[0] == "write"]) == int(write and not fixture)
    assert result["assets"] == ({} if fixture else {"BTC": 1})
