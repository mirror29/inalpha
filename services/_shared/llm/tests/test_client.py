"""LLMClient 单元测试 —— OpenAI-compat provider。

测试策略：
1. mock OpenAI chat.completions.create 响应
2. 验证 token 统计解析
3. MockLLMClient 的预设响应行为
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from inalpha_shared_llm import MutationRequest, MutationResponse
from inalpha_shared_llm.client import LLMClient, MockLLMClient


def _make_mock_completion(content: str, prompt_tokens: int = 150, completion_tokens: int = 50) -> MagicMock:
    """构造一个 Open AI-compat chat completion mock 对象。"""
    mock_msg = MagicMock()
    mock_msg.content = content
    mock_choice = MagicMock()
    mock_choice.message = mock_msg
    mock_usage = MagicMock()
    mock_usage.prompt_tokens = prompt_tokens
    mock_usage.completion_tokens = completion_tokens
    mock_completion = MagicMock()
    mock_completion.choices = [mock_choice]
    mock_completion.usage = mock_usage
    return mock_completion


@pytest.mark.asyncio
async def test_mutate_returns_text() -> None:
    """验证 mutate 返回文本内容。"""
    client = LLMClient()
    mock_completion = _make_mock_completion(
        "--- a/strategy.py\n+++ b/strategy.py\n@@ -1,3 +1,3 @@\n"
    )
    mock_async_client = AsyncMock()
    mock_async_client.chat.completions.create = AsyncMock(return_value=mock_completion)
    client._client = mock_async_client

    response = await client.mutate(
        MutationRequest(system_prompt="你是策略变异助手。", user_prompt="当前策略：...")
    )
    assert isinstance(response, MutationResponse)
    assert "--- a/strategy.py" in response.content
    mock_async_client.chat.completions.create.assert_awaited_once()


@pytest.mark.asyncio
async def test_mutate_parses_token_stats() -> None:
    """验证 token 统计正确解析。"""
    client = LLMClient()
    mock_completion = _make_mock_completion("diff text", prompt_tokens=300, completion_tokens=100)
    mock_async_client = AsyncMock()
    mock_async_client.chat.completions.create = AsyncMock(return_value=mock_completion)
    client._client = mock_async_client

    response = await client.mutate(
        MutationRequest(system_prompt="s", user_prompt="u")
    )
    assert response.cache_metrics.input_tokens == 300
    assert response.cache_metrics.output_tokens == 100


@pytest.mark.asyncio
async def test_mutate_with_mock_client() -> None:
    """MockLLMClient 按预设顺序返回响应。"""
    client = MockLLMClient(responses=["diff1", "diff2"])
    req = MutationRequest(system_prompt="s", user_prompt="u")

    r1 = await client.mutate(req)
    assert r1.content == "diff1"
    r2 = await client.mutate(req)
    assert r2.content == "diff2"


@pytest.mark.asyncio
async def test_mock_client_exhausted() -> None:
    """MockLLMClient 超出预设数量抛 RuntimeError。"""
    client = MockLLMClient(responses=["only_one"])
    req = MutationRequest(system_prompt="s", user_prompt="u")
    await client.mutate(req)
    with pytest.raises(RuntimeError, match="超出预设"):
        await client.mutate(req)


@pytest.mark.asyncio
async def test_cache_metrics_cost_usd() -> None:
    """CacheMetrics.cost_usd 非零且可计算。"""
    from inalpha_shared_llm.types import CacheMetrics
    m = CacheMetrics(
        cache_read_tokens=5000, cache_write_tokens=2000,
        input_tokens=10000, output_tokens=500,
    )
    cost = m.cost_usd
    assert isinstance(cost, float)
    assert cost > 0


@pytest.mark.asyncio
async def test_close_client() -> None:
    """验证 close 不丢异常。"""
    client = MockLLMClient()
    await client.close()


@pytest.mark.asyncio
async def test_empty_content() -> None:
    """验证空 content 能正常处理。"""
    client = LLMClient()
    mock_completion = _make_mock_completion("")
    mock_async_client = AsyncMock()
    mock_async_client.chat.completions.create = AsyncMock(return_value=mock_completion)
    client._client = mock_async_client

    response = await client.mutate(
        MutationRequest(system_prompt="s", user_prompt="u")
    )
    assert response.content == ""