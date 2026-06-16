"""LLM client 抽象层单测。"""
from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from inalpha_research.llm.client import (
    DeepSeekLLMClient,
    FakeLLMClient,
    LanguageScopedClient,
    LLMError,
    _parse_json_response,
    _with_language_directive,
    build_llm_client,
    infer_output_language,
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


# ────────────────────────────────────────────────────────────────────
# _parse_json_response —— 围栏剥离 + 解析失败语义（ADR-0037 502 修复）
# ────────────────────────────────────────────────────────────────────


def test_parse_json_plain_dict() -> None:
    assert _parse_json_response('{"a": 1}', provider="deepseek", model="m") == {"a": 1}


def test_parse_json_strips_markdown_fence() -> None:
    """模型把 JSON 包进 ```json 围栏时也能解析（上下文长时常见）。"""
    fenced = '```json\n{"a": 1}\n```'
    assert _parse_json_response(fenced, provider="deepseek", model="m") == {"a": 1}
    # 无语言标注的围栏也剥
    assert _parse_json_response('```\n{"b": 2}\n```', provider="x", model="m") == {"b": 2}


def test_parse_json_truncated_raises_500_not_502() -> None:
    """被 max_tokens 截断的残缺 JSON → LLMError，status_code=500（非 502）。

    用 502（Bad Gateway）会误导 orchestrator agent 以为 provider 宕机
    （把"截断"叙述成"DeepSeek API 故障"）。
    """
    truncated = '{"thesis": "a very long unfinished'
    with pytest.raises(LLMError) as ei:
        _parse_json_response(truncated, provider="deepseek", model="m")
    assert ei.value.code == "LLM_INVALID_JSON"
    assert ei.value.status_code == 500


def test_parse_json_non_dict_raises_500() -> None:
    with pytest.raises(LLMError) as ei:
        _parse_json_response("[1, 2, 3]", provider="deepseek", model="m")
    assert ei.value.code == "LLM_INVALID_JSON"
    assert ei.value.status_code == 500


# ────────────────────────────────────────────────────────────────────
# 并发信号量 —— 验证 "N 个分析师一波并发"（LLM_MAX_CONCURRENT）
# ────────────────────────────────────────────────────────────────────


def _stub_resp(content: str) -> Any:
    """伪造 OpenAI SDK 响应对象：只需 ``.choices[0].message.content``。"""
    from types import SimpleNamespace

    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
    )


class _ConcurrencyProbe:
    """记录同一时刻在 ``_call_with_retry`` 里并发执行的数量峰值。"""

    def __init__(self) -> None:
        self.current = 0
        self.peak = 0

    async def call(self, **_kwargs: Any) -> Any:
        self.current += 1
        self.peak = max(self.peak, self.current)
        try:
            await asyncio.sleep(0.05)  # 模拟在途 LLM 调用，制造重叠窗口
            return _stub_resp('{"ok": 1}')
        finally:
            self.current -= 1


async def test_semaphore_allows_full_wave_of_8() -> None:
    """``max_concurrent=8`` + 同时发起 8 次 → 8 个一波全并发（峰值并发=8）。

    这正是 "6 核心 analyst + 2 persona = 8 个一次性触发" 依赖的机制：deep_dive 里
    ``asyncio.gather`` 同时发起，所有调用共享同一个 client 的 ``asyncio.Semaphore``。
    """
    client = DeepSeekLLMClient(api_key="test-key", max_concurrent=8)
    probe = _ConcurrencyProbe()
    client._call_with_retry = probe.call  # type: ignore[method-assign]

    await asyncio.gather(
        *[client.complete_json(system="s", user=f"u{i}") for i in range(8)]
    )

    assert probe.peak == 8  # 8 个真的同时在途，没有被拆成两波


async def test_semaphore_throttles_when_cap_below_demand() -> None:
    """``max_concurrent=5`` + 发起 8 次 → 峰值并发被压到 5（多出的排队）。

    反向证明信号量确实在限流：把 ``LLM_MAX_CONCURRENT`` 调回 5 就会拆成两波，
    这就是之前 8 分析师变慢的原因。
    """
    client = DeepSeekLLMClient(api_key="test-key", max_concurrent=5)
    probe = _ConcurrencyProbe()
    client._call_with_retry = probe.call  # type: ignore[method-assign]

    await asyncio.gather(
        *[client.complete_json(system="s", user=f"u{i}") for i in range(8)]
    )

    assert probe.peak == 5


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
    c._provider_name = "deepseek"
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


# ────────────────────────────────────────────────────────────────────
# LanguageScopedClient (Fix C：research 按用户语言输出)
# ────────────────────────────────────────────────────────────────────


def test_with_language_directive_appends_and_keeps_original() -> None:
    out = _with_language_directive("你是技术面分析师。", "English")
    assert out.startswith("你是技术面分析师。")  # 原 prompt 不动
    assert "[OUTPUT LANGUAGE]" in out
    assert "English" in out


async def test_language_scoped_injects_directive_into_inner_system() -> None:
    # FakeLLMClient 按 system 子串匹配；包装后原内容仍是子串 → 仍命中。
    fake = FakeLLMClient({"role:technical": {"stance": "bullish"}})
    client = LanguageScopedClient(fake, "English")
    out = await client.complete_json(system="you are role:technical", user="NVDA")
    assert out == {"stance": "bullish"}
    # 内层真正收到的 system 带上了语言指令。
    inner_system = fake.calls[0]["system"]
    assert "you are role:technical" in inner_system
    assert "[OUTPUT LANGUAGE]" in inner_system
    assert "English" in inner_system


async def test_language_scoped_passes_through_user_and_params() -> None:
    fake = FakeLLMClient({"role:foo": {"ok": True}})
    client = LanguageScopedClient(fake, "中文")
    await client.complete_json(
        system="role:foo", user="hello", temperature=0.7, max_tokens=123
    )
    call = fake.calls[0]
    assert call["user"] == "hello"
    assert call["temperature"] == 0.7
    assert call["max_tokens"] == 123
    assert "中文" in call["system"]


async def test_language_scoped_aclose_delegates() -> None:
    fake = FakeLLMClient({})
    client = LanguageScopedClient(fake, "English")
    await client.aclose()  # 透传给内层 fake，不抛


# ────────────────────────────────────────────────────────────────────
# infer_output_language (Fix C 第二层：从 user_question 兜底推断语言)
# ────────────────────────────────────────────────────────────────────


def test_infer_language_chinese() -> None:
    assert infer_output_language("研究英伟达：最新价格和基本面") == "中文"


def test_infer_language_english() -> None:
    assert infer_output_language("Research NVDA: latest price") == "English"


def test_infer_language_mixed_cjk_wins() -> None:
    # 含任一汉字即判中文(中文用户常夹英文 ticker)
    assert infer_output_language("研究 NVDA 现在怎么样") == "中文"


def test_infer_language_empty_or_none_is_none() -> None:
    assert infer_output_language("") is None
    assert infer_output_language("   ") is None
    assert infer_output_language(None) is None
