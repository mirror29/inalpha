"""unified diff 应用器 —— 用标准库 difflib 替换 unidiff，宽容 LLM 的输出格式。

unidiff 0.7.5 对 diff 格式要求极其严格，而 LLM（尤其 DeepSeek）生成的 diff
常带 markdown fence、省略空行、行号偏移 1-3 行。直接用 Python 内置 difflib 解析
和应用，比第三方库更宽容。
"""
from __future__ import annotations

import re
from typing import Final

from ..exceptions import DiffApplyError

_DEFAULT_FUZZ: Final[int] = 3


def _clean_diff(raw_diff: str) -> str:
    """清洗 LLM 输出：剥 markdown fence + 定位 diff 块。"""
    text = raw_diff.strip()
    lines = text.split("\n")
    # 剥外层 ```
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() in ("```", "```diff"):
        lines = lines[:-1]
    text = "\n".join(lines).strip()
    # 在内容中找 diff 块
    marker = "--- a/"
    idx = text.find(marker)
    if idx >= 0:
        text = text[idx:]
    # 截断在 ``` 之前
    end = text.find("\n```")
    if end >= 0:
        text = text[:end]
    return text.strip()


def _lines(text: str) -> list[str]:
    return text.splitlines(keepends=True)


_HUNK_RE = re.compile(r"^@@ -(\d+),?(\d*) \+(\d+),?(\d*) @@ ?(.*)$")


def _parse_hunk_header(line: str) -> tuple[int, int] | None:
    """解析 @@ -old,count +new,count @@ → (old_start, old_count)。"""
    m = _HUNK_RE.match(line)
    if not m:
        return None
    start = int(m.group(1))
    count = int(m.group(2)) if m.group(2) else 1
    return start, count


def apply_diff(
    original: str,
    unified_diff: str,
    max_fuzz: int = _DEFAULT_FUZZ,
) -> str:
    if not unified_diff.strip():
        return original

    diff_text = _clean_diff(unified_diff)

    lines = _lines(original)
    diff_lines = diff_text.split("\n")

    # 跳过 header (--- / +++ 行)
    i = 0
    while i < len(diff_lines) and (
        diff_lines[i].startswith("---") or diff_lines[i].startswith("+++")
    ):
        i += 1

    if not any(l.startswith("@@") for l in diff_lines):
        raise DiffApplyError(
            "unified diff 不包含任何 hunk",
            original=original, failed_diff=unified_diff,
        )

    while i < len(diff_lines):
        line = diff_lines[i]
        if not line.startswith("@@"):
            i += 1
            continue

        parsed = _parse_hunk_header(line)
        if parsed is None:
            i += 1
            continue

        old_start, _old_count = parsed
        i += 1

        # 收集 hunk body
        hunk_body: list[tuple[str, str]] = []
        while i < len(diff_lines):
            l = diff_lines[i]
            if l.startswith("@@"):
                break
            if l.startswith(" "):
                hunk_body.append(("context", l[1:]))
            elif l.startswith("-"):
                hunk_body.append(("removed", l[1:]))
            elif l.startswith("+"):
                hunk_body.append(("added", l[1:]))
            i += 1

        if not hunk_body:
            continue

        # 在源码中定位匹配位置
        pos = _find_hunk_position(lines, hunk_body, old_start, max_fuzz)

        if pos is None:
            raise DiffApplyError(
                f"hunk 行号 {old_start} 无法匹配，尝试 fuzz={max_fuzz}",
                original=original,
                failed_diff=unified_diff,
            )

        # 应用 hunk：src = context + removed，tgt = context + added
        src_lines = [t for kind, t in hunk_body if kind in ("context", "removed")]
        tgt_lines = [t for kind, t in hunk_body if kind in ("context", "added")]

        # 转换回带 \n 的格式以匹配源文件
        src_with_nl = [l + "\n" if not l.endswith("\n") else l for l in src_lines]
        tgt_with_nl = [l + "\n" if not l.endswith("\n") else l for l in tgt_lines]

        lines = lines[:pos] + tgt_with_nl + lines[pos + len(src_with_nl):]

    return "".join(lines)


def _find_hunk_position(
    lines: list[str],
    hunk_body: list[tuple[str, str]],
    old_start: int,
    max_fuzz: int,
) -> int | None:
    """在源码中定位 hunk 的匹配位置。

    用完整的 source side 行（context + removed）做连续匹配。
    """
    # source side = context + removed（hunk 在原文件中的连续行）
    src_lines = [t for kind, t in hunk_body if kind in ("context", "removed")]
    if not src_lines:
        return None

    lines_stripped = [l.rstrip("\n") for l in lines]
    src_stripped = [l.rstrip("\n") if l.endswith("\n") else l for l in src_lines]

    # 1. header 行号 ± fuzz
    start = max(0, old_start - 1)
    search_start = max(0, start - max_fuzz)
    search_end = min(len(lines_stripped), start + max_fuzz + len(src_stripped))

    for offset in range(search_start, search_end - len(src_stripped) + 1):
        if lines_stripped[offset : offset + len(src_stripped)] == src_stripped:
            return offset

    # 2. 全文件扫描（max_fuzz > 0 时）
    if max_fuzz > 0:
        for offset in range(len(lines_stripped) - len(src_stripped) + 1):
            if lines_stripped[offset : offset + len(src_stripped)] == src_stripped:
                return offset

    return None



def apply_diff_strict(original: str, unified_diff: str) -> str:
    return apply_diff(original, unified_diff, max_fuzz=0)