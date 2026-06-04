"""``compute_structure_hash`` 结构指纹去重单测（docs/miro/11 M4）。

挡"只改注释/缩进/空行"的伪多样性 candidate；但保留字面量差异（参数不同 = 不同策略）。
"""
from __future__ import annotations

from inalpha_paper.storage.strategy_candidates import (
    compute_code_hash,
    compute_structure_hash,
)

_BASE = '''\
class S(Strategy):
    def on_bar(self, bar):
        # buy when fast > slow
        if bar.close > 100:
            self.submit_order(bar)
'''

# 仅改注释 + 空行 + 缩进风格，逻辑/字面量完全一致
_COSMETIC = '''\
class S(Strategy):

    def on_bar(self, bar):
        # totally different comment here
        if bar.close > 100:
                self.submit_order(bar)
'''

# 改了字面量（100 → 200）—— 真·不同策略
_DIFFERENT_LITERAL = '''\
class S(Strategy):
    def on_bar(self, bar):
        if bar.close > 200:
            self.submit_order(bar)
'''


def test_cosmetic_changes_collapse_to_same_structure_hash() -> None:
    assert compute_structure_hash(_BASE) == compute_structure_hash(_COSMETIC)
    # 但精确 code_hash 不同（证明确实是"看似不同"）
    assert compute_code_hash(_BASE) != compute_code_hash(_COSMETIC)


def test_different_literal_yields_different_structure_hash() -> None:
    assert compute_structure_hash(_BASE) != compute_structure_hash(_DIFFERENT_LITERAL)


def test_syntax_error_falls_back_without_raising() -> None:
    bad = "class S(:\n  def oops("
    # 不抛，回退到 raw code hash
    h = compute_structure_hash(bad)
    assert isinstance(h, str) and len(h) == 16
