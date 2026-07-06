"""population / candidate 单元测试。"""
from __future__ import annotations

from uuid import uuid4

import pytest

from inalpha_evolver.population import Candidate, EvolutionRun


def test_candidate_defaults() -> None:
    """验证 Candidate 默认值正确。"""
    c = Candidate()
    assert c.status == "evaluated"
    assert c.generation == 1
    assert c.overfitting_risk == "high"


def test_evolution_run_defaults() -> None:
    """验证 EvolutionRun 默认值正确。"""
    r = EvolutionRun()
    assert r.status == "running"
    assert r.budget == 4
    assert r.candidates_count == 0


def test_candidate_with_values() -> None:
    """验证 Candidate 字段赋值。"""
    rid = uuid4()
    cid = uuid4()
    c = Candidate(
        candidate_id=cid,
        run_id=rid,
        generation=2,
        source_code="class Foo: pass",
        source_hash="abc123",
        fitness=1.5,
        status="proposed",
    )
    assert c.candidate_id == cid
    assert c.run_id == rid
    assert c.generation == 2
    assert c.fitness == 1.5
    assert c.status == "proposed"


def test_evaluation_result_import() -> None:
    """验证 EvaluationResult 可导入和构造。"""
    from inalpha_evolver.population import EvaluationResult

    r = EvaluationResult(
        report={"sharpe": 1.5},
        fitness=1.5,
        data_epoch=1234567890000,
    )
    assert r.report["sharpe"] == 1.5
    assert r.fitness == 1.5