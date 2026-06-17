"""CV splitter 单测（ADR-0028）—— 切分正确 / purging+embargo / 末段含最新 bar / 路径数。"""
from __future__ import annotations

import pytest

from inalpha_paper.engine.cv import (
    CombinatorialPurgedCV,
    InsufficientDataError,
    PurgedKFold,
    WalkForward,
    optimal_folds_number,
)

# ─── WalkForward ───


def test_walkforward_rolling_windows() -> None:
    wf = WalkForward(test_size=10, train_size=20)
    splits = list(wf.split(60))
    assert len(splits) == wf.get_n_splits(60) == 4
    for s in splits:
        assert len(s.test_idx) == 10
        assert len(s.train_idx) == 20  # 滚动定长
        assert s.path_id == 0
        # train 紧贴 test 之前、不重叠
        assert s.train_idx[-1] + 1 == s.test_idx[0]


def test_walkforward_last_test_includes_latest_bar() -> None:
    """末折 test 必须覆盖到最后一根 bar（CLAUDE.md §3.1 金融时效性硬约束）。"""
    wf = WalkForward(test_size=7, train_size=20)
    splits = list(wf.split(63))
    assert splits[-1].test_idx[-1] == 62  # n_samples - 1


def test_walkforward_expanding() -> None:
    wf = WalkForward(test_size=10, train_size=20, expanding=True)
    splits = list(wf.split(60))
    # 扩展窗口：train 从 0 累积，越后越长
    assert splits[0].train_idx[0] == 0
    assert splits[-1].train_idx[0] == 0
    assert len(splits[-1].train_idx) > len(splits[0].train_idx)


def test_walkforward_insufficient_data() -> None:
    wf = WalkForward(test_size=10, train_size=20)
    with pytest.raises(InsufficientDataError):
        list(wf.split(25))


# ─── PurgedKFold ───


def test_purged_kfold_covers_all_test_indices_once() -> None:
    kf = PurgedKFold(n_splits=5, embargo_pct=0.0)
    splits = list(kf.split(100))
    assert len(splits) == 5
    all_test = sorted(i for s in splits for i in s.test_idx)
    assert all_test == list(range(100))  # 每个 bar 恰好做一次 test


def test_purged_kfold_embargo_purges_neighbors() -> None:
    """embargo 把 train 中紧邻 test 的 bar 剔除（防泄漏）。"""
    kf = PurgedKFold(n_splits=5, embargo_pct=0.05)
    splits = list(kf.split(100))
    embargo = int(100 * 0.05)  # 5
    for s in splits:
        ts, te = s.test_idx[0], s.test_idx[-1]
        for i in s.train_idx:
            # train 不得落在 test 前后 embargo 根内
            assert i < ts - embargo or i >= te + 1 + embargo
        # train 与 test 不重叠
        assert not (set(s.train_idx) & set(s.test_idx))


def test_purged_kfold_last_fold_includes_latest_bar() -> None:
    kf = PurgedKFold(n_splits=4, embargo_pct=0.05)
    splits = list(kf.split(80))
    assert splits[-1].test_idx[-1] == 79


def test_purged_kfold_insufficient_data() -> None:
    with pytest.raises(InsufficientDataError):
        list(PurgedKFold(n_splits=5).split(9))


# ─── CombinatorialPurgedCV ───


def test_cpcv_path_and_split_counts() -> None:
    cv = CombinatorialPurgedCV(n_folds=6, n_test_folds=2)
    # 路径数 = C(5,1) = 5；Split 数 = C(6,2)*2 = 30
    assert cv.n_paths() == 5
    splits = list(cv.split(300))
    assert len(splits) == cv.get_n_splits(300) == 30
    # 每条 path 恰好由 n_folds 段组成（每组贡献一段）
    by_path: dict[int, int] = {}
    for s in splits:
        by_path[s.path_id] = by_path.get(s.path_id, 0) + 1
    assert set(by_path) == {0, 1, 2, 3, 4}
    assert all(count == 6 for count in by_path.values())


def test_cpcv_train_excludes_test_and_embargo() -> None:
    cv = CombinatorialPurgedCV(n_folds=6, n_test_folds=2, embargo_pct=0.05)
    splits = list(cv.split(300))
    for s in splits:
        assert not (set(s.train_idx) & set(s.test_idx))  # 不重叠


def test_cpcv_path_segments_reconstruct_full_timeline() -> None:
    """同 path_id 的所有 test 段拼起来应覆盖全时间轴（一条完整 OOS 路径）。"""
    cv = CombinatorialPurgedCV(n_folds=6, n_test_folds=2)
    splits = list(cv.split(300))
    for path_id in range(cv.n_paths()):
        seg = sorted(i for s in splits if s.path_id == path_id for i in s.test_idx)
        assert seg == list(range(300))  # 无缺口、无重叠、含末根


def test_cpcv_below_min_bars_raises() -> None:
    with pytest.raises(InsufficientDataError):
        list(CombinatorialPurgedCV(n_folds=6, n_test_folds=2).split(150))


def test_cpcv_invalid_params() -> None:
    with pytest.raises(ValueError):
        CombinatorialPurgedCV(n_folds=1, n_test_folds=1)
    with pytest.raises(ValueError):
        CombinatorialPurgedCV(n_folds=5, n_test_folds=5)  # n_test 必须 < n_folds


# ─── optimal_folds_number ───


def test_optimal_folds_number_returns_valid_pair() -> None:
    n_folds, n_test = optimal_folds_number(
        2000, target_n_test_paths=10, target_train_size=252
    )
    assert 2 <= n_folds <= 20
    assert 1 <= n_test < n_folds
