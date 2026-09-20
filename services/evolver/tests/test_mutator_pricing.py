"""冻结定价与 token 统计测试。"""

from __future__ import annotations

from uuid import uuid4

import pytest
from inalpha_shared.errors import ValidationError
from inalpha_shared_llm.types import CacheMetrics, MutationResponse

from inalpha_evolver.exceptions import DiffApplyError, LLMError
from inalpha_evolver.mutator import Mutator
from inalpha_evolver.runtime.slots import persist_mutation

_SOURCE = """class Strategy:\n    value = 1\n"""
_DIFF = """--- a/strategy.py
+++ b/strategy.py
@@ -1,2 +1,2 @@
 class Strategy:
-    value = 1
+    value = 2
"""


class _PricedClient:
    def __init__(self) -> None:
        self.max_tokens = 0
        self.calls = 0

    async def mutate(self, request):
        self.calls += 1
        self.max_tokens = request.max_tokens
        return MutationResponse(
            content=_DIFF,
            cache_metrics=CacheMetrics(input_tokens=1_000, output_tokens=200),
        )

    async def close(self) -> None:
        return None


class _InvalidDiffClient(_PricedClient):
    async def mutate(self, request):
        self.max_tokens = request.max_tokens
        return MutationResponse(
            content="""--- a/strategy.py
+++ b/strategy.py
@@ -99,1 +99,1 @@
-missing = 1
+missing = 2
""",
            cache_metrics=CacheMetrics(input_tokens=1_000, output_tokens=200),
        )


class _FullSourceClient(_PricedClient):
    async def mutate(self, request):
        self.max_tokens = request.max_tokens
        return MutationResponse(
            content="""```python
class Strategy:
    value = 2

    def on_bar(self, bar):
        return bar
```""",
            cache_metrics=CacheMetrics(input_tokens=1_000, output_tokens=200),
        )


@pytest.mark.asyncio
async def test_mutator_uses_frozen_rates_and_returns_usage() -> None:
    client = _PricedClient()
    mutator = Mutator(
        llm_client=client,  # type: ignore[arg-type]
        input_usd_per_million=2.0,
        output_usd_per_million=10.0,
        max_output_tokens=4_096,
    )

    result = await mutator.mutate(_SOURCE)

    assert result.input_tokens == 1_000
    assert result.output_tokens == 200
    assert result.llm_cost_usd == pytest.approx(0.004)
    assert client.max_tokens == 4_096


@pytest.mark.asyncio
async def test_mutator_rejects_input_above_approved_budget_before_calling_provider() -> None:
    client = _PricedClient()
    mutator = Mutator(
        llm_client=client,  # type: ignore[arg-type]
        input_usd_per_million=2.0,
        output_usd_per_million=10.0,
        max_input_utf8_bytes=100,
    )

    with pytest.raises(LLMError, match="超过已审批上限"):
        await mutator.mutate(_SOURCE)

    assert client.calls == 0


@pytest.mark.asyncio
async def test_diff_failure_keeps_frozen_cost_and_usage() -> None:
    mutator = Mutator(
        llm_client=_InvalidDiffClient(),  # type: ignore[arg-type]
        input_usd_per_million=2.0,
        output_usd_per_million=10.0,
    )

    with pytest.raises(DiffApplyError) as error:
        await mutator.mutate(_SOURCE)

    assert error.value.llm_cost_usd == pytest.approx(0.004)
    assert error.value.input_tokens == 1_000
    assert error.value.output_tokens == 200


@pytest.mark.asyncio
async def test_full_source_response_is_converted_to_canonical_diff() -> None:
    result = await Mutator(
        llm_client=_FullSourceClient(),  # type: ignore[arg-type]
        input_usd_per_million=2.0,
        output_usd_per_million=10.0,
    ).mutate(_SOURCE)

    assert result.unified_diff is not None
    assert result.unified_diff.startswith("--- a/strategy.py\n+++ b/strategy.py")
    assert "value = 2" in result.new_source


class _ConnectionContext:
    async def __aenter__(self) -> object:
        return object()

    async def __aexit__(self, *_args: object) -> None:
        return None


@pytest.mark.asyncio
async def test_persist_mutation_writes_input_and_output_tokens(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}
    mutation = await Mutator(
        llm_client=_PricedClient(),  # type: ignore[arg-type]
        input_usd_per_million=2.0,
        output_usd_per_million=10.0,
    ).mutate(_SOURCE)

    async def source_exists(*_args: object) -> bool:
        return False

    async def update_slot(*_args: object, **values: object) -> dict[str, object]:
        captured.update(values)
        return values

    monkeypatch.setattr("inalpha_evolver.runtime.slots.get_conn", _ConnectionContext)
    monkeypatch.setattr(
        "inalpha_evolver.runtime.slots.audit_strategy_source", lambda source: source
    )
    monkeypatch.setattr("inalpha_evolver.runtime.slots.candidates.source_exists", source_exists)
    monkeypatch.setattr("inalpha_evolver.runtime.slots.candidates.update_slot", update_slot)

    await persist_mutation(uuid4(), 0, mutation)

    assert captured["input_tokens"] == 1_000
    assert captured["output_tokens"] == 200


@pytest.mark.asyncio
async def test_no_change_outcome_still_persists_usage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}
    mutation = await Mutator(
        llm_client=_PricedClient(),  # type: ignore[arg-type]
        input_usd_per_million=2.0,
        output_usd_per_million=10.0,
    ).mutate(_SOURCE)
    mutation.unified_diff = None

    async def update_slot(*_args: object, **values: object) -> dict[str, object]:
        captured.update(values)
        return values

    monkeypatch.setattr("inalpha_evolver.runtime.slots.get_conn", _ConnectionContext)
    monkeypatch.setattr("inalpha_evolver.runtime.slots.candidates.update_slot", update_slot)

    assert await persist_mutation(uuid4(), 0, mutation) is None
    assert captured["outcome"] == "no_change"
    assert captured["llm_cost_usd"] == pytest.approx(0.004)
    assert captured["input_tokens"] == 1_000
    assert captured["output_tokens"] == 200


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["ast_rejected", "duplicate"])
async def test_rejected_or_duplicate_mutation_still_persists_usage(
    monkeypatch: pytest.MonkeyPatch,
    outcome: str,
) -> None:
    captured: dict[str, object] = {}
    mutation = await Mutator(
        llm_client=_PricedClient(),  # type: ignore[arg-type]
        input_usd_per_million=2.0,
        output_usd_per_million=10.0,
    ).mutate(_SOURCE)

    async def source_exists(*_args: object) -> bool:
        return outcome == "duplicate"

    async def update_slot(*_args: object, **values: object) -> dict[str, object]:
        captured.update(values)
        return values

    def audit(source: str) -> str:
        if outcome == "ast_rejected":
            raise ValidationError("unsafe source", code="CANDIDATE_AST_REJECTED")
        return source

    monkeypatch.setattr("inalpha_evolver.runtime.slots.get_conn", _ConnectionContext)
    monkeypatch.setattr("inalpha_evolver.runtime.slots.audit_strategy_source", audit)
    monkeypatch.setattr("inalpha_evolver.runtime.slots.candidates.source_exists", source_exists)
    monkeypatch.setattr("inalpha_evolver.runtime.slots.candidates.update_slot", update_slot)

    assert await persist_mutation(uuid4(), 0, mutation) is None
    assert captured["outcome"] == outcome
    assert captured["llm_cost_usd"] == pytest.approx(0.004)
    assert captured["input_tokens"] == 1_000
    assert captured["output_tokens"] == 200
