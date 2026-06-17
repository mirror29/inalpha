"""时序交叉验证 splitter（ADR-0028）—— WalkForward / PurgedKFold / CombinatorialPurgedCV。

**纯索引数学，与执行引擎正交**：splitter 只负责把 ``n_samples`` 根 bar 切成 train/test
索引对，不碰回测执行（执行在 :func:`backtest.run_cv_backtest`）。API 对齐 skfolio，便于
熟悉 sklearn 生态的用户上手；**不引入 skfolio 包**（控外部依赖），借鉴算法自实现。

设计依据：
- CPCV（López de Prado 2018）：切 N 份、每次取 K>1 份做 test、purging + embargo 消除
  信息泄漏、输出 C(N,K) 条 train/test 组合并重构成 φ=C(N-1,K-1) 条 OOS 路径。
- Arian et al. 2024：CPCV 在 PBO/DSR 双指标上优于 walk-forward / K-fold。

**硬约束（CLAUDE.md §3.1 金融时效性）**：所有 splitter 必须保证**末段 test 含最新 bar**
（``test_idx`` 覆盖到 ``n_samples - 1``）——回测末段不能丢最新行情。
"""
from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from itertools import combinations
from math import comb

#: CPCV 最少 bar 数（< 此值切分无统计意义，调用方回落 WalkForward）。
_CPCV_MIN_BARS = 200


class InsufficientDataError(ValueError):
    """bar 数不足以构建请求的 CV 切分（调用方据此回落更简单的 splitter）。"""


@dataclass(frozen=True, slots=True)
class Split:
    """一个 train/test 索引对。

    Attributes:
        train_idx: 训练（含 warmup / 上下文）bar 的原始索引，升序。
        test_idx: 样本外评估 bar 的原始索引，升序。
        path_id: CPCV 用于标记同一条 OOS 路径的多个片段（同 path_id 的 test 段按时间
            拼接成一条完整路径）；WalkForward / PurgedKFold 恒为 0。
    """

    train_idx: list[int]
    test_idx: list[int]
    path_id: int


class WalkForward:
    """滚动 / 扩展窗口前向验证（对齐 ``skfolio.model_selection.WalkForward``）。

    每折：train 在前、test 紧随其后，按 ``test_size`` 向前滑动。**末折 test 锚定到序列
    末尾**（保证含最新 bar），起始多余的最旧 bar 被舍弃。

    Args:
        test_size: 每折 test 窗口的 bar 数。
        train_size: train 窗口的 bar 数（``expanding=True`` 时为首折最小训练量）。
        expanding: True = 扩展窗口（train 从 0 累积）；False = 滚动窗口（定长 train）。
    """

    def __init__(self, test_size: int, train_size: int, *, expanding: bool = False) -> None:
        if test_size < 1:
            raise ValueError(f"test_size must be >= 1, got {test_size}")
        if train_size < 1:
            raise ValueError(f"train_size must be >= 1, got {train_size}")
        self.test_size = test_size
        self.train_size = train_size
        self.expanding = expanding

    def get_n_splits(self, n_samples: int) -> int:
        return max(0, (n_samples - self.train_size) // self.test_size)

    def split(self, n_samples: int) -> Iterator[Split]:
        n_folds = self.get_n_splits(n_samples)
        if n_folds < 1:
            raise InsufficientDataError(
                f"WalkForward 需要 n_samples >= train_size + test_size "
                f"({self.train_size} + {self.test_size}), 实际 {n_samples}"
            )
        for k in range(n_folds):
            # 末折(k=n_folds-1) test_end 锚到 n_samples → 含最新 bar；前折依次前移
            test_end = n_samples - (n_folds - 1 - k) * self.test_size
            test_start = test_end - self.test_size
            train_start = 0 if self.expanding else max(0, test_start - self.train_size)
            yield Split(
                train_idx=list(range(train_start, test_start)),
                test_idx=list(range(test_start, test_end)),
                path_id=0,
            )


class PurgedKFold:
    """带 purging + embargo 的 K 折时序 CV（López de Prado 2018）。

    每折取一段连续区间做 test，其余做 train，并**剔除 train 中与 test 相邻 embargo 根内的
    bar**（消除时序信息泄漏）。

    Args:
        n_splits: 折数（>= 2）。
        embargo_pct: embargo 占总 bar 数的比例（默认 0.05，按 bar 数不按日历，跨市场一致）。
    """

    def __init__(self, n_splits: int, *, embargo_pct: float = 0.05) -> None:
        if n_splits < 2:
            raise ValueError(f"n_splits must be >= 2, got {n_splits}")
        if not 0.0 <= embargo_pct < 1.0:
            raise ValueError(f"embargo_pct must be in [0, 1), got {embargo_pct}")
        self.n_splits = n_splits
        self.embargo_pct = embargo_pct

    def get_n_splits(self, n_samples: int) -> int:
        return self.n_splits

    def split(self, n_samples: int) -> Iterator[Split]:
        if n_samples < self.n_splits * 2:
            raise InsufficientDataError(
                f"PurgedKFold 需要 n_samples >= 2*n_splits ({2 * self.n_splits}), "
                f"实际 {n_samples}"
            )
        bounds = _fold_bounds(n_samples, self.n_splits)
        embargo = int(n_samples * self.embargo_pct)
        for start, end in bounds:
            test_idx = list(range(start, end))
            train_idx = [
                i
                for i in range(n_samples)
                if i < start - embargo or i >= end + embargo
            ]
            yield Split(train_idx=train_idx, test_idx=test_idx, path_id=0)


class CombinatorialPurgedCV:
    """组合式 purged CV（CPCV，对齐 ``skfolio.model_selection.CombinatorialPurgedCV``）。

    切 ``n_folds`` 组，每次取 ``n_test_folds`` 组做 test，其余做 train（purge+embargo）。
    每个 (组合, test 组) 产出一个 Split，并按"该组第几次作为 test"分配 ``path_id``——
    同 path_id 的 test 段按时间拼接 = 一条完整 OOS 路径，共 ``n_paths`` 条。

    Args:
        n_folds: 总分组数（>= 2）。
        n_test_folds: 每个组合取作 test 的组数（1 <= 此值 < n_folds，> 1 才是组合式）。
        embargo_pct: embargo 比例（默认 0.05）。
    """

    def __init__(
        self, n_folds: int, n_test_folds: int, *, embargo_pct: float = 0.05
    ) -> None:
        if n_folds < 2:
            raise ValueError(f"n_folds must be >= 2, got {n_folds}")
        if not 1 <= n_test_folds < n_folds:
            raise ValueError(
                f"n_test_folds must be in [1, n_folds), got {n_test_folds} (n_folds={n_folds})"
            )
        if not 0.0 <= embargo_pct < 1.0:
            raise ValueError(f"embargo_pct must be in [0, 1), got {embargo_pct}")
        self.n_folds = n_folds
        self.n_test_folds = n_test_folds
        self.embargo_pct = embargo_pct

    def n_paths(self) -> int:
        """重构出的完整 OOS 路径数 = C(n_folds-1, n_test_folds-1)。"""
        return comb(self.n_folds - 1, self.n_test_folds - 1)

    def get_n_splits(self, n_samples: int) -> int:
        """总 Split 数 = C(n_folds, n_test_folds) * n_test_folds（每组合每 test 组各一）。"""
        return comb(self.n_folds, self.n_test_folds) * self.n_test_folds

    def split(self, n_samples: int) -> Iterator[Split]:
        if n_samples < _CPCV_MIN_BARS:
            raise InsufficientDataError(
                f"CPCV 需要 n_samples >= {_CPCV_MIN_BARS}, 实际 {n_samples}"
                "（调用方应回落 WalkForward）"
            )
        if n_samples < self.n_folds * 2:
            raise InsufficientDataError(
                f"CPCV 需要 n_samples >= 2*n_folds ({2 * self.n_folds}), 实际 {n_samples}"
            )
        bounds = _fold_bounds(n_samples, self.n_folds)
        embargo = int(n_samples * self.embargo_pct)
        # 每个组只作为 test 出现 φ=C(n_folds-1, n_test_folds-1) 次；按出现序分配 path_id 0..φ-1
        occurrence = [0] * self.n_folds
        for combo in combinations(range(self.n_folds), self.n_test_folds):
            test_groups = set(combo)
            test_ranges = [bounds[g] for g in combo]
            train_idx = [
                i
                for i in range(n_samples)
                if _group_of(i, bounds) not in test_groups
                and not _within_embargo(i, test_ranges, embargo)
            ]
            for g in combo:
                start, end = bounds[g]
                yield Split(
                    train_idx=train_idx,
                    test_idx=list(range(start, end)),
                    path_id=occurrence[g],
                )
                occurrence[g] += 1


def optimal_folds_number(
    n_observations: int,
    *,
    target_n_test_paths: int = 100,
    target_train_size: int = 252,
) -> tuple[int, int]:
    """求接近目标路径数 + 目标训练量的 ``(n_folds, n_test_folds)``（对齐 skfolio 同名函数）。

    在 ``n_folds ∈ [2, 20]``、``n_test_folds ∈ [1, n_folds)`` 中搜，优先满足
    "单组合 train 量 >= target_train_size"，再最小化 |路径数 - 目标|。

    Returns:
        ``(n_folds, n_test_folds)``；找不到满足训练量约束的则返回路径数最接近的。
    """
    best: tuple[int, int] | None = None
    best_key: tuple[int, float] | None = None  # (训练量未达标=1, |路径差|)
    for n_folds in range(2, 21):
        group_size = n_observations / n_folds
        for n_test in range(1, n_folds):
            paths = comb(n_folds - 1, n_test - 1)
            train_size = group_size * (n_folds - n_test)
            shortfall = 1 if train_size < target_train_size else 0
            key = (shortfall, abs(paths - target_n_test_paths))
            if best_key is None or key < best_key:
                best_key = key
                best = (n_folds, n_test)
    assert best is not None
    return best


def _fold_bounds(n_samples: int, n_folds: int) -> list[tuple[int, int]]:
    """把 ``n_samples`` 均分成 ``n_folds`` 段连续区间，返回 ``[(start, end), ...]``。

    余数摊到前几段（与 ``numpy.array_split`` 同口径），保证覆盖 ``[0, n_samples)`` 且
    末段 end == n_samples（含最新 bar）。
    """
    base, rem = divmod(n_samples, n_folds)
    bounds: list[tuple[int, int]] = []
    start = 0
    for g in range(n_folds):
        size = base + (1 if g < rem else 0)
        bounds.append((start, start + size))
        start += size
    return bounds


def _group_of(i: int, bounds: list[tuple[int, int]]) -> int:
    """索引 ``i`` 落在第几组。"""
    for g, (start, end) in enumerate(bounds):
        if start <= i < end:
            return g
    return -1


def _within_embargo(
    i: int, test_ranges: list[tuple[int, int]], embargo: int
) -> bool:
    """``i`` 是否落在任一 test 区间前后 ``embargo`` 根内（purge+embargo 两侧）。"""
    return any(start - embargo <= i < end + embargo for start, end in test_ranges)
