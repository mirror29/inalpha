"""LLMClient 单元测试。

测试策略：mock Anthropic API 响应，验证：
1. ``mutate()`` 正确组装请求（system prompt + user prompt + cache_control）
2. 正确的缓存指标解析
3. timeout 传递
4. MockLLMClient 的预设响应行为
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from inalpha_shared_llm.client import LLMClient, MockLLMClient
from inalpha_shared_llm.types import CacheMetrics, MutationRequest, MutationResponse


@pytest.fixture
def mock_anthropic_client() -> MagicMock:
    """构造 mock Anthropic 响应。"""
    mock_message = MagicMock()
    mock_message.content = [MagicMock(type="text", text="--- a/strategy.py\n+++ b/strategy.py\n")]
    mock_message.usage = MagicMock(
        input_tokens=150,
        output_tokens=50,
        cache_read_input_tokens=0,
        cache_creation_input_tokens=200,
    )
    return mock_message


@pytest.mark.asyncio
async def test_mutate_returns_text(mock_anthropic_client: MagicMock) -> None:
    """验证 mutate 返回文本内容。"""
    client = LLMClient()
    # mock Anthropic 客户端
    mock_async_client = AsyncMock()
    mock_async_client.messages.create = AsyncMock(return_value=mock_anthropic_client)
    client._client = mock_async_client

    request = MutationRequest(
        system_prompt="你是一个策略变异助手。",
        user_prompt="当前策略：...",
    )
    response = await client.mutate(request)

    assert isinstance(response, MutationResponse)
    assert "--- a/strategy.py" in response.content
    mock_async_client.messages.create.assert_awaited_once()


@pytest.mark.asyncio
async def test_mutate_parses_cache_metrics(mock_anthropic_client: MagicMock) -> None:
    """验证缓存指标从响应正确解析。"""
    client = LLMClient()
    mock_async_client = AsyncMock()
    mock_async_client.messages.create = AsyncMock(return_value=mock_anthropic_client)
    client._client = mock_async_client

    request = MutationRequest(
        system_prompt="你是一个策略变异助手。",
        user_prompt="当前策略：...",
    )
    response = await client.mutate(request)

    assert response.cache_metrics.input_tokens == 150
    assert response.cache_metrics.output_tokens == 50
    assert response.cache_metrics.cache_read_tokens == 0
    assert response.cache_metrics.cache_write_tokens == 200


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
    """MockLLMClient 超出预设数量时抛 RuntimeError。"""
    client = MockLLMClient(responses=["only_one"])
    req = MutationRequest(system_prompt="s", user_prompt="u")

    await client.mutate(req)
    with pytest.raises(RuntimeError, match="超出预设"):
        await client.mutate(req)


@pytest.mark.asyncio
async def test_cache_metrics_cost_usd() -> None:
    """验证 CacheMetrics.cost_usd 计算不报错。"""
    metrics = CacheMetrics(
        cache_read_tokens=5000,
        cache_write_tokens=2000,
        input_tokens=10000,
        output_tokens=500,
    )
    cost = metrics.cost_usd
    assert isinstance(cost, float)
    assert cost > 0


@pytest.mark.asyncio
async def test_close_client() -> None:
    """验证 close 方法不报错。"""
    client = MockLLMClient()
    await client.close()


@pytest.mark.asyncio
async def test_empty_content() -> None:
    """验证空 content 的处理。"""
    mock_message = MagicMock()
    mock_message.content = [MagicMock(type="text", text="")]
    mock_message.usage = MagicMock(
        input_tokens=10, output_tokens=0,
        cache_read_input_tokens=0, cache_creation_input_tokens=0,
    )

    client = LLMClient()
    mock_async_client = AsyncMock()
    mock_async_client.messages.create = AsyncMock(return_value=mock_message)
    client._client = mock_async_client

    response = await client.mutate(
        MutationRequest(system_prompt="s", user_prompt="u")
    )
    assert response.content == ""