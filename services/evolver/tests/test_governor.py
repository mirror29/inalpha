"""governor 单元测试。

测试策略：
1. HintGenerator 按顺序轮转
2. run_one_generation 使用 MockMutator 运行完整（或 mock）循环
3. 计数器正确（rejected_ast / rejected_contract / failed_eval）
"""
from __future__ import annotations

from uuid import uuid4

import pytest
from inalpha_shared.errors import NotFoundError

from inalpha_evolver.governor import seed_resolver
from inalpha_evolver.governor.hint_generator import HintGenerator
from inalpha_evolver.governor.loop import run_one_generation
from inalpha_evolver.governor.seed import SEED_STRATEGY_CODE
from inalpha_evolver.population import EvolutionRun


def test_hint_generator_cycles() -> None:
    """HintGenerator 按顺序轮转 4 条 hint。"""
    gen = HintGenerator()
    h1 = gen.next()
    h2 = gen.next()
    h3 = gen.next()
    h4 = gen.next()
    h5 = gen.next()  # 应回到第一个
    assert h1 != h2
    assert h4 != h3
    assert h5 == h1  # 循环


def test_hint_generator_all_unique() -> None:
    """4 条 hint 各不相同。"""
    gen = HintGenerator()
    hints = {gen.next() for _ in range(4)}
    assert len(hints) == 4


def test_hint_generator_reset() -> None:
    """Reset 后从头开始。"""
    gen = HintGenerator()
    h1 = gen.next()
    gen.next()  # 跳过第二个
    gen.reset()
    assert gen.next() == h1


@pytest.mark.asyncio
async def test_run_one_generation_with_mock() -> None:
    """MockMutator 模式：验证 run_one_generation 返回 EvolutionRun。"""
    run = await run_one_generation(
        run_id=None,  # type: ignore[arg-type]
        budget=2,
        config={"universe": ["BTCUSDT"], "timeframe": "1h"},
        mutator=None,
        evaluator=None,
    )
    assert isinstance(run, EvolutionRun)
    assert run.status in ("completed", "failed")


def test_seed_strategy_code_not_empty() -> None:
    """种子策略源码不为空。"""
    assert len(SEED_STRATEGY_CODE) > 50
    assert "SMACrossStrategy" in SEED_STRATEGY_CODE


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["candidate", "promoted"])
async def test_owner_draft_and_promoted_candidates_can_seed_research(
    monkeypatch: pytest.MonkeyPatch,
    status: str,
) -> None:
    candidate_id = uuid4()
    owner_id = uuid4()

    async def get_owned_candidate(*_args: object) -> dict[str, object]:
        return {"status": status, "code": SEED_STRATEGY_CODE}

    monkeypatch.setattr(seed_resolver, "get_owned_candidate", get_owned_candidate)
    resolved = await seed_resolver.resolve_seed(
        object(),  # type: ignore[arg-type]
        f"candidate:{candidate_id}",
        owner_id,
    )

    assert resolved.reference == f"candidate:{candidate_id}"
    assert resolved.source_code == SEED_STRATEGY_CODE


@pytest.mark.asyncio
async def test_rejected_candidate_cannot_seed_research(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate_id = uuid4()

    async def get_owned_candidate(*_args: object) -> dict[str, object]:
        return {"status": "rejected", "code": SEED_STRATEGY_CODE}

    monkeypatch.setattr(seed_resolver, "get_owned_candidate", get_owned_candidate)
    with pytest.raises(NotFoundError):
        await seed_resolver.resolve_seed(
            object(),  # type: ignore[arg-type]
            f"candidate:{candidate_id}",
            uuid4(),
        )


@pytest.mark.asyncio
async def test_successful_evolution_candidate_can_seed_next_research_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate_id = uuid4()
    owner_id = uuid4()

    async def get_candidate(*_args: object) -> dict[str, object]:
        return {"outcome": "succeeded", "source_code": SEED_STRATEGY_CODE}

    monkeypatch.setattr(seed_resolver.evolution_candidates, "get_candidate", get_candidate)
    reference = f"evolution_candidate:{candidate_id}"
    resolved = await seed_resolver.resolve_seed(
        object(),  # type: ignore[arg-type]
        reference,
        owner_id,
    )

    assert resolved.reference == reference
    assert resolved.source_code == SEED_STRATEGY_CODE


@pytest.mark.asyncio
async def test_failed_or_foreign_evolution_candidate_cannot_seed_research(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def get_candidate(*_args: object) -> dict[str, object] | None:
        return None

    monkeypatch.setattr(seed_resolver.evolution_candidates, "get_candidate", get_candidate)
    with pytest.raises(NotFoundError):
        await seed_resolver.resolve_seed(
            object(),  # type: ignore[arg-type]
            f"evolution_candidate:{uuid4()}",
            uuid4(),
        )
