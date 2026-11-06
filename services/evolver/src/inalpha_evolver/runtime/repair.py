"""Bounded repair consumes an existing approved slot and retains its failed parent."""

from typing import Any

from ..storage import candidates

_REPAIR_CODES = frozenset({
    "MUTATION_OUTPUT_TRUNCATED", "MUTATION_DIFF_INVALID",
    "MUTATION_CONTEXT_MISMATCH", "MUTATION_POSITION_INVALID",
})


def repair_parent(rows: list[dict[str, Any]], slot: dict[str, Any]) -> dict[str, Any] | None:
    """Select at most one repair per run; an existing link survives worker recovery."""
    if slot.get("parent_id") is not None:
        return next((row for row in rows if row["candidate_id"] == slot["parent_id"]), None)
    if any(row.get("parent_id") is not None for row in rows):
        return None
    return next((row for row in rows if (
        row["slot"] < slot["slot"] and row["outcome"] == "diff_failed"
        and row.get("error_code") in _REPAIR_CODES
    )), None)


async def prepare_repair(conn: Any, run: dict[str, Any], slot: dict[str, Any]) -> str:
    """Persist repair lineage before requesting the model, without replaying untrusted output."""
    rows = await candidates.list_candidates(conn, run["run_id"], run["owner_account_id"])
    parent = repair_parent(rows, slot)
    if parent is None:
        return slot["mutation_hint"]
    if slot.get("parent_id") is not None:
        return slot["mutation_hint"]
    hint = (
        f"修复前次候选 slot {parent['slot']} 的补丁协议错误 ({parent['error_code']})。"
        "仍基于原始种子源码，只做一个最小变更；准确复制声明位置的上下文，"
        "输出短的单文件 unified diff，不重写整个策略，不增加导入。"
        "本次占用已批准的候选名额，失败后不再模型修复。"
    )
    await candidates.update_slot(
        conn, run["run_id"], slot["slot"],
        parent_id=parent["candidate_id"], mutation_hint=hint,
    )
    return hint
