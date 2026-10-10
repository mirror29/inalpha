"""Short structured calls must not silently consume their budget on thinking."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from inalpha_research.llm.client import DeepSeekLLMClient


@pytest.mark.asyncio
@pytest.mark.parametrize("provider,model,disabled", [
    ("deepseek", "deepseek-flash", True),
    ("deepseek", "deepseek-reasoner", False),
    ("openai", "deepseek-flash", False),
])
async def test_structured_flash_mode_is_provider_scoped(provider, model, disabled):
    client = DeepSeekLLMClient(api_key="local-test", provider_name=provider, model=model)
    create = AsyncMock(return_value=SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content='{"ok":true}'))]
    ))
    client._client.chat.completions.create = create
    try:
        assert await client.complete_json(system="Return JSON", user="Check", max_tokens=600) == {"ok": True}
        params = create.call_args.kwargs
        assert params["max_tokens"] == 600
        assert params.get("extra_body") == ({"thinking": {"type": "disabled"}} if disabled else None)
    finally:
        await client.aclose()
