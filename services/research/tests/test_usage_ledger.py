"""Offline receipts prove unknowns, retries and concurrency without paid requests."""

import asyncio
from types import SimpleNamespace as NS

import pytest
from inalpha_shared.usage import (
    UsageIdentity,
    UsageRecorder,
    estimate_cost,
    normalize_usage,
    usage_stage,
)


class Store:
    def __init__(self):
        self.begins = []
        self.receipts = []

    async def begin(self, *args):
        self.begins.append(args)

    async def settle(self, identity, call_id, receipt):
        self.receipts.append((call_id, receipt))


@pytest.mark.parametrize(
    "family,response,expected",
    [
        (
            "openai",
            {
                "usage": {
                    "prompt_tokens": 100,
                    "completion_tokens": 50,
                    "prompt_tokens_details": {"cached_tokens": 80},
                    "completion_tokens_details": {"reasoning_tokens": 30},
                }
            },
            (100, 50),
        ),
        (
            "anthropic",
            {
                "usage": {
                    "input_tokens": 20,
                    "output_tokens": 50,
                    "cache_read_input_tokens": 80,
                    "cache_creation_input_tokens": 10,
                }
            },
            (110, 50),
        ),
        (
            "gemini",
            {
                "usage_metadata": {
                    "prompt_token_count": 100,
                    "candidates_token_count": 20,
                    "thoughts_token_count": 30,
                }
            },
            (100, 50),
        ),
    ],
)
def test_totals_do_not_double_count(family, response, expected):
    result = normalize_usage(response, family)
    assert (result["input_tokens"], result["output_tokens"]) == expected
    assert result["usage_status"] == "known"


def test_unknown_is_not_free():
    usage = normalize_usage(None, "openai")
    assert usage["input_tokens"] is None
    assert (
        estimate_cost(
            usage,
            {
                "currency": "USD",
                "version": "v1",
                "input_usd_per_million": 1,
                "output_usd_per_million": 2,
            },
        )
        is None
    )
    assert (
        normalize_usage({"usage": {"prompt_tokens": 0, "completion_tokens": 0}}, "openai")[
            "usage_status"
        ]
        == "known"
    )


async def test_concurrency_scopes_and_failed_attempts():
    store = Store()
    recorder = UsageRecorder(UsageIdentity("alice", "research", "op", "openai", "m"), store)

    async def invoke():
        await asyncio.sleep(0)
        return NS(usage=NS(prompt_tokens=3, completion_tokens=4), choices=[])

    async def task(stage):
        with usage_stage(stage):
            await recorder.request(invoke)

    await asyncio.gather(task("analyst"), task("debate"))
    assert {row[-1] for row in store.begins} == {"analyst", "debate"}
    assert len({row[1] for row in store.begins}) == 2

    async def fail():
        raise TimeoutError("contains no persisted exception")

    with pytest.raises(TimeoutError):
        await recorder.request(fail, attempt=1)
    assert store.receipts[-1][1]["status"] == "failed"
    assert store.receipts[-1][1]["estimated_cost_usd"] is None


async def test_storage_failure_prevents_payment():
    class Broken(Store):
        async def begin(self, *args):
            raise OSError("offline")

    called = False

    async def invoke():
        nonlocal called
        called = True

    recorder = UsageRecorder(UsageIdentity("alice", "research", "op", "openai", "m"), Broken())
    with pytest.raises(OSError):
        await recorder.request(invoke)
    assert not called


async def test_settlement_retry_never_repeats_model_request():
    class Broken(Store):
        async def settle(self, *args):
            raise OSError("offline")

    calls = 0

    async def invoke():
        nonlocal calls
        calls += 1

    recorder = UsageRecorder(UsageIdentity("alice", "research", "op", "openai", "m"), Broken())
    await recorder.request(invoke)
    assert calls == 1


async def test_cancel_is_visible():
    store = Store()
    recorder = UsageRecorder(UsageIdentity("alice", "research", "op", "openai", "m"), store)

    async def invoke():
        raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await recorder.request(invoke)
    assert store.receipts[0][1]["status"] == "cancelled"


async def test_invalid_json_is_charged_and_linked_to_pilot_artifact(tmp_path):
    from inalpha_research.llm.client import DeepSeekLLMClient, LLMError
    from inalpha_research.pilot.artifacts import PilotArtifacts
    from tests.test_pilot_artifacts import case

    artifacts = PilotArtifacts(
        tmp_path, experiment_id="e", case=case(), arm_id="D", configuration={"model": "fixture"}
    )
    store = Store()
    recorder = UsageRecorder(
        UsageIdentity(
            "alice", "research", artifacts.run_id, "openai", "fixture", links=artifacts.links
        ),
        store,
        event_sink=artifacts.event,
    )
    client = DeepSeekLLMClient(api_key="test-key", model="fixture", recorder=recorder)

    async def create(**kwargs):
        return NS(
            model="fixture-revision",
            usage=NS(prompt_tokens=10, completion_tokens=5),
            choices=[NS(message=NS(content="not json"), finish_reason="stop")],
        )

    await client.aclose()
    client._client = NS(chat=NS(completions=NS(create=create)))
    with pytest.raises(LLMError):
        await client.complete_json(system="fixture system", user=artifacts.model_input())
    assert store.receipts[0][1]["status"] == "invalid"
    assert store.receipts[0][1]["input_tokens"] == 10
    import json

    events = [
        json.loads(line) for line in (artifacts.path / "calls.jsonl").read_text().splitlines()
    ]
    assert events[0]["call_id"] == store.receipts[0][0] == events[1]["call_id"]
    assert events[0]["request"]["messages"][0]["content"] == "fixture system"
    assert events[1]["output"] == "not json"
