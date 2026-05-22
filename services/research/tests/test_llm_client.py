"""LLM client 抽象层单测。"""
from __future__ import annotations

import pytest

from inalpha_research.llm.client import (
    FakeLLMClient,
    LLMError,
    build_llm_client,
)

# ────────────────────────────────────────────────────────────────────
# FakeLLMClient
# ────────────────────────────────────────────────────────────────────


async def test_fake_returns_canned_for_matched_system() -> None:
    fake = FakeLLMClient({"role:foo": {"x": 1}})
    out = await fake.complete_json(system="you are role:foo", user="anything")
    assert out == {"x": 1}


async def test_fake_records_call_args() -> None:
    fake = FakeLLMClient({"role:foo": {"x": 1}})
    await fake.complete_json(system="role:foo", user="hello", temperature=0.7, max_tokens=100)
    assert len(fake.calls) == 1
    call = fake.calls[0]
    assert call["temperature"] == 0.7
    assert call["max_tokens"] == 100
    assert call["user"] == "hello"


async def test_fake_no_match_raises_with_code() -> None:
    fake = FakeLLMClient({})
    with pytest.raises(LLMError) as ei:
        await fake.complete_json(system="nothing here", user="x")
    # InalphaError stash code 在 detail dict 里（构造器把 code 塞进 detail），
    # 不是 class attribute；所以走 .detail["code"] 才能拿到 runtime override
    assert ei.value.detail["code"] == "LLM_FAKE_NO_MATCH"


async def test_fake_first_match_wins_on_ambiguity() -> None:
    """字典是有序的 —— 同 system 同时含多个 key 时取首个。"""
    fake = FakeLLMClient({"role:a": {"id": "a"}, "role:b": {"id": "b"}})
    out = await fake.complete_json(system="role:a role:b", user="x")
    assert out["id"] == "a"


async def test_fake_set_response_updates_at_runtime() -> None:
    fake = FakeLLMClient({})
    fake.set_response("role:dynamic", {"ok": True})
    out = await fake.complete_json(system="role:dynamic blah", user="x")
    assert out == {"ok": True}


# ────────────────────────────────────────────────────────────────────
# build_llm_client factory
# ────────────────────────────────────────────────────────────────────


def test_build_fake_provider_returns_fake() -> None:
    c = build_llm_client(
        provider="fake",
        api_key="",
        base_url="",
        model="",
        timeout_seconds=1.0,
    )
    assert isinstance(c, FakeLLMClient)


def test_build_unknown_provider_raises() -> None:
    with pytest.raises(ValueError, match="unknown LLM provider"):
        build_llm_client(
            provider="bogus",
            api_key="",
            base_url="",
            model="",
            timeout_seconds=1.0,
        )


def test_build_deepseek_requires_api_key() -> None:
    with pytest.raises(ValueError, match="api_key is required"):
        build_llm_client(
            provider="deepseek",
            api_key="",
            base_url="https://api.deepseek.com/v1",
            model="deepseek-chat",
            timeout_seconds=1.0,
        )
