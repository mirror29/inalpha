"""unified diff 应用器 —— 带 fuzz match 的 diff apply。

核心流程：
1. 用 ``unidiff`` 库解析 unified diff 字符串，拆成多个 PatchSet / PatchedFile / Hunk
2. 对每个 hunk，先在原始源码中**精确匹配**（按 header 行号定位）
3. 精确失败则 fuzz 搜索：以 context 行为锚点用序列比较在 ±max_fuzz 行偏移
   内找最佳匹配位置
4. 在最佳位置应用 hunk 变更
5. 任一 hunk 彻底失败 → 抛 ``DiffApplyError``（含源头 + 失败 hunk 信息）
"""
from __future__ import annotations

from typing import Final

from unidiff import PatchSet
from unidiff.errors import UnidiffParseError

from ..exceptions import DiffApplyError

# 最大 fuzz 偏移行数 —— LLM 生成的 diff 行号偏移通常在 ±3 行以内
_DEFAULT_FUZZ: Final[int] = 3


def _lines(text: str) -> list[str]:
    """按 ``\n`` 切分行，保留末行空行语义。"""
    return text.splitlines(keepends=True)


def apply_diff(
    original: str,
    unified_diff: str,
    max_fuzz: int = _DEFAULT_FUZZ,
) -> str:
    """应用 unified diff 到原始源码。

    Args:
        original: 原始源码字符串。
        unified_diff: 标准 unified diff 格式。
        max_fuzz: 行号偏移容差（行）。默认 3。

    Returns:
        应用 diff 后的源码字符串。

    Raises:
        DiffApplyError: 解析失败或 hunk 无法匹配。
    """
    if not unified_diff.strip():
        return original

    try:
        patch_set = PatchSet(unified_diff)
    except UnidiffParseError as exc:
        raise DiffApplyError(
            f"unified diff 解析失败：{exc}",
            original=original,
            failed_diff=unified_diff,
        ) from exc

    lines = _lines(original)

    if not patch_set:
        raise DiffApplyError(
            "unified diff 不包含任何 hunk",
            original=original,
            failed_diff=unified_diff,
        )

    # 目前处理单文件变更（多文件场景留 E2 扩展）
    patched_file = patch_set[0]

    for hunk in patched_file:
        # 提取 context 行（去掉空行和 +/- 行）
        context_lines: list[str] = []
        for item in hunk:
            if item.is_context and item.value.strip():
                context_lines.append(item.value)

        if not context_lines:
            # 全新增/删除的 hunk —— 按 header 行号操作
            pass

        # 用 source_lines 构建替换映射
        src_lines = [item.value for item in hunk if not item.is_added]
        tgt_lines = [item.value for item in hunk if not item.is_removed]

        # unidiff 0.7.5 在末尾可能会解析出一个空行（来自 trailing \\n\\n），
        # 且 hunk 中行的末尾带 \\n，而原始源码的尾行可能没有 \\n。
        # 对齐时统一去掉末尾的 \\n 做比较
        while src_lines and src_lines[-1] == "\n":
            src_lines.pop()
        while tgt_lines and tgt_lines[-1] == "\n":
            tgt_lines.pop()
        # 尾行可能有 \\n 也可能没有，统一去掉比较
        src_lines_stripped = [l.rstrip("\n") for l in src_lines]
        lines_stripped = [l.rstrip("\n") for l in lines]

        # 找匹配起始位置
        source_start = hunk.source_start  # 1-based

        # 尝试精确匹配
        pos = _find_exact_match(lines_stripped, src_lines_stripped, source_start, max_fuzz)

        if pos is None:
            # 尝试用 context 行 fuzz 搜索
            if context_lines:
                pos = _find_fuzzy_match(lines_stripped, context_lines, source_start, max_fuzz)

        if pos is None:
            raise DiffApplyError(
                f"hunk 行号 {source_start} 无法匹配上下文 "
                f"(fuzz={max_fuzz})",
                original=original,
                failed_diff=unified_diff,
            )

        # 在 pos 位置应用替换
        # src_lines 包含 context + removed 行，tgt_lines 包含 context + added 行
        # 确保 pos + len(src_lines) 不越界（fuzz 偏移后可能超出）
        if pos + len(src_lines) > len(lines):
            raise DiffApplyError(
                f"hunk 行号 {source_start} 匹配位置 {pos} 超出源码范围 "
                f"(lines={len(lines)}, src_len={len(src_lines)})",
                original=original,
                failed_diff=unified_diff,
            )
        lines = lines[:pos] + tgt_lines + lines[pos + len(src_lines) :]

    return "".join(lines)


def _find_exact_match(
    lines: list[str],
    src_lines: list[str],
    header_start: int,
    max_fuzz: int,
) -> int | None:
    """在 header_start 附近搜索精确匹配。

    先试精确行号，再试 ±max_fuzz 偏移内的精确匹配。
    """
    if not src_lines:
        return None

    start = max(0, header_start - 1)
    # 精确位置
    if start + len(src_lines) <= len(lines):
        if lines[start : start + len(src_lines)] == src_lines:
            return start

    # fuzz 偏移搜索
    search_start = max(0, start - max_fuzz)
    search_end = min(len(lines), start + max_fuzz + len(src_lines))

    for offset in range(search_start, search_end - len(src_lines) + 1):
        if lines[offset : offset + len(src_lines)] == src_lines:
            return offset

    return None


def _find_fuzzy_match(
    lines: list[str],
    context_lines: list[str],
    header_start: int,
    max_fuzz: int,
) -> int | None:
    """用 context 行在 header_start 附近的 ±max_fuzz 偏移内搜索。"""
    if not context_lines:
        return None

    search_start = max(0, header_start - 1 - max_fuzz)
    search_end = min(len(lines), header_start - 1 + len(context_lines) + max_fuzz)

    for offset in range(search_start, search_end - len(context_lines) + 1):
        if lines[offset : offset + len(context_lines)] == context_lines:
            return offset

    return None


def apply_diff_strict(
    original: str, unified_diff: str
) -> str:
    """无 fuzz 的严格 diff 应用。

    与 ``apply_diff`` 相同，但 ``max_fuzz=0``。
    用于测试：预期 LLM 返回的 diff 行号完全准确。
    """
    return apply_diff(original, unified_diff, max_fuzz=0)