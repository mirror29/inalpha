"""严格、原子地应用单文件 unified diff。"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..exceptions import DiffApplyError

_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(?: .*)?$")
_OLD_PATH = "--- a/strategy.py"
_NEW_PATH = "+++ b/strategy.py"


@dataclass(frozen=True, slots=True)
class _Hunk:
    old_start: int
    old_count: int
    new_start: int
    new_count: int
    body: tuple[tuple[str, str], ...]


def _fail(message: str, original: str, diff: str) -> DiffApplyError:
    return DiffApplyError(message, original=original, failed_diff=diff)


def _clean(raw_diff: str, original: str) -> list[str]:
    text = raw_diff.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines[0] not in {"```", "```diff"} or lines[-1] != "```":
            raise _fail("diff markdown fence 格式无效", original, raw_diff)
        text = "\n".join(lines[1:-1]).strip()
    lines = text.splitlines()
    if len(lines) < 3 or lines[:2] != [_OLD_PATH, _NEW_PATH]:
        raise _fail(
            "diff 必须且只能修改 a/strategy.py -> b/strategy.py",
            original,
            raw_diff,
        )
    if any(line.startswith(("--- ", "+++ ")) for line in lines[2:]):
        raise _fail("diff 不允许包含第二个文件", original, raw_diff)
    return lines[2:]


def repair_hunk_counts(raw_diff: str) -> str:
    """修正 LLM 常见的 hunk 行数声明错误，不改变路径、位置或正文。

    这里只重算 ``@@`` header 的 old/new count；后续 ``apply_diff`` 仍会对
    文件路径、hunk 位置与原始源码上下文执行严格校验。
    """
    lines = raw_diff.splitlines()
    repaired: list[str] = []
    index = 0
    while index < len(lines):
        match = _HUNK_RE.fullmatch(lines[index])
        if match is None:
            repaired.append(lines[index])
            index += 1
            continue
        old_start, _, new_start, _ = match.groups()
        body_start = index + 1
        body_end = body_start
        while body_end < len(lines) and not lines[body_end].startswith("@@"):
            body_end += 1
        body = lines[body_start:body_end]
        old_count = sum(bool(line) and line[0] in {" ", "-"} for line in body)
        new_count = sum(bool(line) and line[0] in {" ", "+"} for line in body)
        repaired.append(f"@@ -{old_start},{old_count} +{new_start},{new_count} @@")
        repaired.extend(body)
        index = body_end
    return "\n".join(repaired)


def _parse_hunks(lines: list[str], original: str, raw_diff: str) -> list[_Hunk]:
    hunks: list[_Hunk] = []
    index = 0
    while index < len(lines):
        if lines[index] == "":
            index += 1
            continue
        match = _HUNK_RE.fullmatch(lines[index])
        if match is None:
            raise _fail(f"无效 hunk header: {lines[index]!r}", original, raw_diff)
        old_start, old_raw, new_start, new_raw = match.groups()
        old_count = int(old_raw) if old_raw is not None else 1
        new_count = int(new_raw) if new_raw is not None else 1
        index += 1
        body: list[tuple[str, str]] = []
        seen_old = seen_new = 0
        while index < len(lines) and not lines[index].startswith("@@"):
            line = lines[index]
            if line == "" and seen_old == old_count and seen_new == new_count:
                index += 1
                break
            if not line or line[0] not in {" ", "+", "-"}:
                raise _fail(f"无效 hunk 行: {line!r}", original, raw_diff)
            prefix, content = line[0], line[1:]
            body.append((prefix, content))
            seen_old += prefix in {" ", "-"}
            seen_new += prefix in {" ", "+"}
            if seen_old > old_count or seen_new > new_count:
                raise _fail("hunk 行数超过 header 声明", original, raw_diff)
            index += 1
        if seen_old != old_count or seen_new != new_count:
            raise _fail("hunk 行数与 header 声明不一致", original, raw_diff)
        if not any(prefix in {"+", "-"} for prefix, _ in body):
            raise _fail("hunk 未包含任何变更", original, raw_diff)
        hunks.append(_Hunk(int(old_start), old_count, int(new_start), new_count, tuple(body)))
    if not hunks:
        raise _fail("unified diff 不包含任何 hunk", original, raw_diff)
    return hunks


def apply_diff(original: str, unified_diff: str, max_fuzz: int = 0) -> str:
    """按 header 的精确位置原子应用 diff；``max_fuzz`` 仅为兼容旧调用。"""
    del max_fuzz
    if not unified_diff.strip():
        return original
    hunks = _parse_hunks(_clean(unified_diff, original), original, unified_diff)
    source = original.splitlines()
    output: list[str] = []
    cursor = 0
    delta = 0
    for hunk in hunks:
        old_index = hunk.old_start - 1 if hunk.old_count else hunk.old_start
        new_index = hunk.new_start - 1 if hunk.new_count else hunk.new_start
        if old_index < cursor or old_index > len(source):
            raise _fail("hunk 重叠或 old_start 越界", original, unified_diff)
        if new_index != old_index + delta:
            raise _fail("hunk new_start 与前序变更不一致", original, unified_diff)
        expected = [text for prefix, text in hunk.body if prefix in {" ", "-"}]
        if source[old_index : old_index + len(expected)] != expected:
            raise _fail("hunk 上下文与声明位置不完全匹配", original, unified_diff)
        output.extend(source[cursor:old_index])
        output.extend(text for prefix, text in hunk.body if prefix in {" ", "+"})
        cursor = old_index + len(expected)
        delta += hunk.new_count - hunk.old_count
    output.extend(source[cursor:])
    result = "\n".join(output)
    return result + "\n" if original.endswith("\n") else result


def apply_diff_strict(original: str, unified_diff: str) -> str:
    """兼容旧 API；所有 diff 现在都使用严格模式。"""
    return apply_diff(original, unified_diff)
