"""LLM client 抽象层单测。"""
from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from inalpha_research.llm.client import (
    DeepSeekLLMClient,
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
    # D-8b' review 修复后构造器会把 code 写到 self；现在双轨都能查
    assert ei.value.code == "LLM_FAKE_NO_MATCH"
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


# ────────────────────────────────────────────────────────────────────
# DeepSeekLLMClient 护栏（D-9）：semaphore 限流 + 退避重试
# ────────────────────────────────────────────────────────────────────


def _make_response(content: str = '{"ok": true}') -> Any:
    """伪造一个 openai SDK 的 ChatCompletion 形状对象（鸭子类型）。"""
    msg = type("M", (), {"content": content})()
    choice = type("C", (), {"message": msg})()
    return type("R", (), {"choices": [choice]})()


def _make_client(
    *,
    max_concurrent: int = 5,
    max_retries: int = 3,
    retry_base_seconds: float = 0.01,
) -> DeepSeekLLMClient:
    """绕过 ``__init__`` 创建 client —— 跳过 openai SDK 真实构造。

    Why: 单测不依赖 openai 网络栈；只验护栏（semaphore / retry / 异常分类）。
    """
    c = DeepSeekLLMClient.__new__(DeepSeekLLMClient)
    c._client = None  # type: ignore[assignment]
    c._model = "test-model"
    c._semaphore = asyncio.Semaphore(max_concurrent)
    c._max_retries = max_retries
    c._retry_base_seconds = retry_base_seconds
    return c


class _FakeChat:
    """模拟 ``client.chat.completions.create`` —— 可控行为。"""

    def __init__(
        self,
        *,
        response_content: str = '{"ok": true}',
        raise_seq: list[Exception] | None = None,
        track_in_flight: bool = False,
    ) -> None:
        self._response_content = response_content
        self._raise_seq = list(raise_seq or [])
        self.call_count = 0
        self.in_flight = 0
        self.max_in_flight = 0
        self._track = track_in_flight
        # mock self.chat.completions.create 链路
        self.completions = self
        self.chat = self

    async def create(self, **_kwargs: Any) -> Any:
        self.call_count += 1
        if self._track:
            self.in_flight += 1
            self.max_in_flight = max(self.max_in_flight, self.in_flight)
            await asyncio.sleep(0.05)
            self.in_flight -= 1
        if self._raise_seq:
            err = self._raise_seq.pop(0)
            if err is not None:
                raise err
        return _make_response(self._response_content)


async def test_semaphore_limits_concurrent_calls() -> None:
    """max_concurrent=2 + 10 个并发调用 → 同时 in-flight 数 ≤ 2。"""
    c = _make_client(max_concurrent=2, max_retries=0)
    fake = _FakeChat(track_in_flight=True)
    c._client = fake  # type: ignore[assignment]

    coros = [c.complete_json(system="s", user="u") for _ in range(10)]
    results = await asyncio.gather(*coros)

    assert len(results) == 10
    assert all(r == {"ok": True} for r in results)
    assert fake.call_count == 10
    assert fake.max_in_flight <= 2, f"max_in_flight={fake.max_in_flight}, expected ≤ 2"


async def test_retriable_error_retries_until_success() -> None:
    """前 2 次抛 RateLimitError，第 3 次成功 → 总共 3 次调用、最终成功。"""
    from openai import RateLimitError

    err1 = RateLimitError.__new__(RateLimitError)
    err2 = RateLimitError.__new__(RateLimitError)

    c = _make_client(max_retries=3, retry_base_seconds=0.01)
    fake = _FakeChat(raise_seq=[err1, err2, None])  # None = 不抛、返成功
    c._client = fake  # type: ignore[assignment]

    out = await c.complete_json(system="s", user="u")
    assert out == {"ok": True}
    assert fake.call_count == 3


async def test_retriable_error_exhausts_retries_then_raises_llm_error() -> None:
    """所有重试都失败 → 抛 LLMError（包装原异常）、不裸传原 openai 异常。"""
    from openai import RateLimitError

    errs = [RateLimitError.__new__(RateLimitError) for _ in range(5)]
    c = _make_client(max_retries=2, retry_base_seconds=0.01)
    fake = _FakeChat(raise_seq=errs)
    c._client = fake  # type: ignore[assignment]

    with pytest.raises(LLMError) as ei:
        await c.complete_json(system="s", user="u")
    assert ei.value.code == "LLM_PROVIDER_ERROR"
    # max_retries=2 → 总共 1 + 2 = 3 次调用
    assert fake.call_count == 3


async def test_non_retriable_error_raises_immediately() -> None:
    """非可重试异常（如 ValueError）→ 不重试、直接包成 LLMError 抛。"""
    c = _make_client(max_retries=3, retry_base_seconds=0.01)
    fake = _FakeChat(raise_seq=[ValueError("bad request")])
    c._client = fake  # type: ignore[assignment]

    with pytest.raises(LLMError) as ei:
        await c.complete_json(system="s", user="u")
    assert ei.value.code == "LLM_PROVIDER_ERROR"
    assert fake.call_count == 1, "non-retriable error must not trigger retries"


async def test_invalid_json_raises_llm_invalid_json() -> None:
    """SDK 成功返回但 content 不是 JSON → 抛 LLM_INVALID_JSON。"""
    c = _make_client(max_retries=0)
    fake = _FakeChat(response_content="not json at all")
    c._client = fake  # type: ignore[assignment]

    with pytest.raises(LLMError) as ei:
        await c.complete_json(system="s", user="u")
    assert ei.value.code == "LLM_INVALID_JSON"


async def test_max_retries_zero_means_one_attempt_only() -> None:
    """max_retries=0 → 只跑 1 次、可重试错误不重试。"""
    from openai import APITimeoutError

    err = APITimeoutError.__new__(APITimeoutError)
    c = _make_client(max_retries=0, retry_base_seconds=0.01)
    fake = _FakeChat(raise_seq=[err])
    c._client = fake  # type: ignore[assignment]

    with pytest.raises(LLMError):
        await c.complete_json(system="s", user="u")
    assert fake.call_count == 1


# ────────────────────────────────────────────────────────────────────
# 防 unused import lint
# ────────────────────────────────────────────────────────────────────
_ = json  # keep import used (helps type checkers / linters)
