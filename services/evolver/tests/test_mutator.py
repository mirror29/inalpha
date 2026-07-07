"""diff_applier 单元测试。

测试策略：
1. 精确匹配（行号完全准确）
2. fuzz match（±1/2/3 行偏移）
3. 不可恢复的 diff（行号偏移过大 / 内容不匹配）
4. 空 diff / 无效 diff
5. 多 hunk diff
6. 新增/删除/修改 三种变更类型
"""
from __future__ import annotations

import pytest

from inalpha_evolver.mutator.diff_applier import DiffApplyError, apply_diff, apply_diff_strict

# ── 测试用例 ──────────────────────────────────────────────────────

_SOURCE = """
class Foo:
    def bar(self):
        x = 1
        y = 2
        z = 3
        return x + y + z
""".strip()


def _diff(*lines: str) -> str:
    """构造带正确行尾分隔的 diff 字符串。"""
    return "\n".join(lines) + "\n\n"


def test_exact_match() -> None:
    """精确匹配：行号完全准确，直接应用。"""
    diff = _diff(
        "--- a/strategy.py",
        "+++ b/strategy.py",
        "@@ -2,6 +2,6 @@",
        "     def bar(self):",
        "         x = 1",
        "-        y = 2",
        "+        y = 20",
        "         z = 3",
        "         return x + y + z",
    )
    result = apply_diff(_SOURCE, diff)
    # 注意 'y = 2' 是 'y = 20' 的子串，用 '    y = 2\\n' 做精确断言
    assert "y = 20" in result
    assert "    y = 2\n" not in result


def test_fuzz_plus_2() -> None:
    """fuzz +2 行偏移：行号偏移 2 行仍能匹配。"""
    # 原始行号 2 → 实际偏移到 4
    diff = _diff(
        "--- a/strategy.py",
        "+++ b/strategy.py",
        "@@ -4,6 +4,6 @@",
        "     def bar(self):",
        "         x = 1",
        "-        y = 2",
        "+        y = 20",
        "         z = 3",
        "         return x + y + z",
    )
    result = apply_diff(_SOURCE, diff, max_fuzz=3)
    assert "y = 20" in result


def test_fuzz_minus_1() -> None:
    """fuzz -1 行偏移。"""
    diff = _diff(
        "--- a/strategy.py",
        "+++ b/strategy.py",
        "@@ -1,6 +1,6 @@",
        "     def bar(self):",
        "         x = 1",
        "-        y = 2",
        "+        y = 20",
        "         z = 3",
        "         return x + y + z",
    )
    result = apply_diff(_SOURCE, diff, max_fuzz=3)
    assert "y = 20" in result


def test_fuzz_too_large() -> None:
    """fuzz 超出 max_fuzz 但全文件扫描仍能找到（max_fuzz>0 时启用全扫描）。"""
    diff = _diff(
        "--- a/strategy.py",
        "+++ b/strategy.py",
        "@@ -10,6 +10,6 @@",
        "     def bar(self):",
        "         x = 1",
        "-        y = 2",
        "+        y = 20",
        "         z = 3",
        "         return x + y + z",
    )
    # max_fuzz=2 > 0 → 启用全文件滑动窗口搜索 → 能匹配
    result = apply_diff(_SOURCE, diff, max_fuzz=2)
    assert "y = 20" in result


def test_strict_fails_with_offset() -> None:
    """严格模式（fuzz=0）行号偏移时失败。"""
    diff = _diff(
        "--- a/strategy.py",
        "+++ b/strategy.py",
        "@@ -3,6 +3,6 @@",
        "     def bar(self):",
        "         x = 1",
        "-        y = 2",
        "+        y = 20",
        "         z = 3",
        "         return x + y + z",
    )
    with pytest.raises(DiffApplyError):
        apply_diff_strict(_SOURCE, diff)


def test_empty_diff() -> None:
    """空 diff → 返回原源码。"""
    result = apply_diff(_SOURCE, "")
    assert result == _SOURCE


def test_whitespace_diff() -> None:
    """仅空白 diff → 返回原源码。"""
    result = apply_diff(_SOURCE, "   \n  \n")
    assert result == _SOURCE


def test_add_line() -> None:
    """新增行。"""
    diff = _diff(
        "--- a/strategy.py",
        "+++ b/strategy.py",
        "@@ -2,6 +2,7 @@",
        "     def bar(self):",
        "         x = 1",
        "+        a = 0",
        "         y = 2",
        "         z = 3",
        "         return x + y + z",
    )
    result = apply_diff(_SOURCE, diff)
    assert "a = 0" in result


def test_delete_line() -> None:
    """删除行。"""
    diff = _diff(
        "--- a/strategy.py",
        "+++ b/strategy.py",
        "@@ -2,5 +2,4 @@",
        "     def bar(self):",
        "         x = 1",
        "-        y = 2",
        "         z = 3",
        "         return x + y + z",
    )
    result = apply_diff(_SOURCE, diff)
    assert "    y = 2\n" not in result


def test_multi_hunk() -> None:
    """多 hunk diff。"""
    diff = _diff(
        "--- a/strategy.py",
        "+++ b/strategy.py",
        "@@ -2,4 +2,5 @@",
        "     def bar(self):",
        "         x = 1",
        "+        a = 0",
        "         y = 2",
        "",
        "@@ -5,3 +6,4 @@",
        "         z = 3",
        "+        w = 4",
        "         return x + y + z",
    )
    result = apply_diff(_SOURCE, diff)
    assert "a = 0" in result
    assert "w = 4" in result


def test_nonexistent_hunk() -> None:
    """hunk 行号完全错误但内容匹配 → 全扫描能修复位置。"""
    diff = _diff(
        "--- a/strategy.py",
        "+++ b/strategy.py",
        "@@ -99,6 +99,6 @@",
        "     def bar(self):",
        "         x = 1",
        "-        y = 2",
        "+        y = 20",
        "         z = 3",
        "         return x + y + z",
    )
    # max_fuzz=3 > 0 → 全扫描能匹配 → 成功应用
    result = apply_diff(_SOURCE, diff, max_fuzz=3)
    assert "y = 20" in result


def test_parse_error() -> None:
    """无效 diff 格式 → 抛异常。"""
    with pytest.raises(DiffApplyError):
        apply_diff(_SOURCE, "这不是一个有效的 diff\n")


def test_no_hunks() -> None:
    """只有 header 没有 hunk → 抛异常（解析失败或空 patch）。"""
    diff = _diff("--- a/strategy.py", "+++ b/strategy.py")
    with pytest.raises(DiffApplyError):
        apply_diff(_SOURCE, diff)