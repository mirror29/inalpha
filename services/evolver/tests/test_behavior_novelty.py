from __future__ import annotations

from inalpha_evolver.runtime.campaign import _vector_distance


def test_behavior_distance_separates_distinct_signal_paths() -> None:
    assert _vector_distance([1.0, 0.0, 0.0], [0.0, 0.0, 1.0]) > 0.5


def test_behavior_distance_is_zero_for_identical_fingerprints() -> None:
    assert _vector_distance([0.2, 0.5, 0.0], [0.2, 0.5, 0.0]) == 0.0


def test_behavior_distance_fails_diverse_when_schema_is_missing() -> None:
    assert _vector_distance(None, [0.2]) == 1.0
