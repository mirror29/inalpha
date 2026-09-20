"""严格 unified diff 应用器测试。"""

from __future__ import annotations

import pytest

from inalpha_evolver.mutator.diff_applier import (
    DiffApplyError,
    apply_diff,
    apply_diff_strict,
    repair_hunk_counts,
)

_SOURCE = """
class Foo:
    def bar(self):
        x = 1
        y = 2
        z = 3
        return x + y + z
""".strip()


def _diff(*lines: str) -> str:
    return "\n".join(lines) + "\n"


def _replace_y(*, old_start: int = 2, old_count: int = 5) -> str:
    return _diff(
        "--- a/strategy.py",
        "+++ b/strategy.py",
        f"@@ -{old_start},{old_count} +2,5 @@",
        "     def bar(self):",
        "         x = 1",
        "-        y = 2",
        "+        y = 20",
        "         z = 3",
        "         return x + y + z",
    )


def test_exact_match_is_applied() -> None:
    result = apply_diff(_SOURCE, _replace_y(), max_fuzz=99)
    assert "y = 20" in result
    assert "    y = 2\n" not in result


def test_markdown_diff_fence_is_supported() -> None:
    result = apply_diff(_SOURCE, f"```diff\n{_replace_y()}```")
    assert "y = 20" in result


def test_multi_hunk_is_applied_atomically() -> None:
    diff = _diff(
        "--- a/strategy.py",
        "+++ b/strategy.py",
        "@@ -2,3 +2,4 @@",
        "     def bar(self):",
        "         x = 1",
        "+        a = 0",
        "         y = 2",
        "@@ -5,2 +6,3 @@",
        "         z = 3",
        "+        w = 4",
        "         return x + y + z",
    )
    result = apply_diff_strict(_SOURCE, diff)
    assert "a = 0" in result
    assert "w = 4" in result


def test_insert_at_start() -> None:
    diff = _diff(
        "--- a/strategy.py",
        "+++ b/strategy.py",
        "@@ -0,0 +1,1 @@",
        "+# generated",
    )
    assert apply_diff(_SOURCE, diff).startswith("# generated\nclass Foo")


def test_delete_line() -> None:
    diff = _diff(
        "--- a/strategy.py",
        "+++ b/strategy.py",
        "@@ -3,3 +3,2 @@",
        "         x = 1",
        "-        y = 2",
        "         z = 3",
    )
    assert "y = 2" not in apply_diff(_SOURCE, diff)


@pytest.mark.parametrize(
    "diff",
    [
        _replace_y(old_start=3),
        _replace_y(old_count=6),
        _replace_y().replace("--- a/strategy.py", "--- a/other.py"),
        _replace_y().replace("+++ b/strategy.py", "+++ b/other.py"),
        _replace_y() + _diff("--- a/strategy.py", "+++ b/strategy.py"),
        "explanation\n" + _replace_y(),
        _replace_y().replace("@@ -2,5 +2,5 @@", "@@ malformed @@"),
        _replace_y().replace("         z = 3", "?        z = 3"),
    ],
)
def test_malformed_or_ambiguous_diff_is_rejected(diff: str) -> None:
    with pytest.raises(DiffApplyError):
        apply_diff(_SOURCE, diff, max_fuzz=3)


def test_any_failed_hunk_rejects_the_whole_patch() -> None:
    diff = _diff(
        "--- a/strategy.py",
        "+++ b/strategy.py",
        "@@ -2,2 +2,2 @@",
        "     def bar(self):",
        "-        x = 1",
        "+        x = 10",
        "@@ -5,2 +5,2 @@",
        "         wrong = 3",
        "-        return x + y + z",
        "+        return 0",
    )
    with pytest.raises(DiffApplyError):
        apply_diff(_SOURCE, diff)
    assert "x = 10" not in _SOURCE


def test_empty_diff_keeps_source() -> None:
    assert apply_diff(_SOURCE, " \n") == _SOURCE


def test_header_without_hunk_is_rejected() -> None:
    with pytest.raises(DiffApplyError):
        apply_diff(_SOURCE, _diff("--- a/strategy.py", "+++ b/strategy.py"))


def test_repair_hunk_counts_keeps_strict_context_validation() -> None:
    malformed = _replace_y().replace("@@ -2,5 +2,5 @@", "@@ -2,99 +2,42 @@")

    repaired = repair_hunk_counts(malformed)

    assert "@@ -2,5 +2,5 @@" in repaired
    assert "y = 20" in apply_diff(_SOURCE, repaired)
