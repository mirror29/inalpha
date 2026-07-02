"""strategy_candidates 表读写（D-9 · ADR-0020 E1 MVP）。

行为约定：

- ``insert_candidate`` 计算 ``code_hash`` = sha256(code) 前 16 hex；UNIQUE 撞了
  返回已有 candidate（不抛 ——LLM 经常会写一模一样的策略，幂等）
- ``update_after_backtest`` 写最近一次 metrics / fitness / backtest_run_id；
  ``updated_at`` 自动刷新
- ``set_status`` 改 status（promote / reject 走这里）；MVP 不暴露 promote 给 LLM

参考 ``backtest_runs.py`` 的事务约定。
"""
from __future__ import annotations

import hashlib
import io
import json
import tokenize
from typing import Any
from uuid import UUID, uuid4

from psycopg import AsyncConnection


def compute_code_hash(code: str) -> str:
    """sha256(code) 前 16 hex 用于 UNIQUE 精确去重。"""
    return hashlib.sha256(code.encode("utf-8")).hexdigest()[:16]


def compute_structure_hash(code: str) -> str:
    """结构指纹：剥注释 + 归一空白后再 hash（docs/miro/11 M4）。

    挡"逻辑/字面量完全一样、只是改了注释 / 缩进 / 空行 / 引号风格"的伪多样性
    candidate —— LLM 反复产出这种"看似不同实则同款"的策略是"来回就那几个"的一个来源。

    **保守**：只归一注释与空白，**保留** NAME / NUMBER / STRING 字面量（变量名、参数数值、
    字符串都参与指纹），避免把"结构相同但参数不同"的真·不同策略误并。tokenize 失败
    （理论上 candidate 已过 AST 审计能解析）时回退到 raw code hash，不抛。
    """
    try:
        toks: list[str] = []
        readline = io.StringIO(code).readline
        for tok in tokenize.generate_tokens(readline):
            if tok.type in (
                tokenize.COMMENT,
                tokenize.NL,
                tokenize.NEWLINE,
                tokenize.INDENT,
                tokenize.DEDENT,
                tokenize.ENCODING,
                tokenize.ENDMARKER,
            ):
                continue
            toks.append(f"{tok.type}:{tok.string}")
        canonical = "".join(toks)
    except Exception:
        canonical = code
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


async def insert_candidate(
    conn: AsyncConnection,
    *,
    code: str,
    description: str = "",
    author: str = "llm",
    author_id: UUID | None = None,
    owner_account_id: UUID | None = None,
    audit: dict[str, Any] | None = None,
    factor_snapshot: dict[str, Any] | None = None,
) -> tuple[UUID, bool]:
    """落一行候选。

    Returns:
        ``(candidate_id, created)``；``created=False`` 表示已存在同 hash，
        返回的是老 ID（幂等）。

    幂等理由：LLM 经常重复写相同策略；调用方应当作"获取或新建"语义用。

    docs/miro/11 M4：除 ``code_hash`` 精确去重，再加**结构指纹**去重——剥注释/空白后
    相同则视为同款（挡"只改注释/缩进"的伪多样性）。结构指纹存进 ``audit.structure_hash``
    JSONB（复用现有列，无需 migration），不命中精确 hash 时再按它查一次。

    ``factor_snapshot``（ADR-0047）：生成时因子血缘。幂等命中已有行时**不更新**——
    血缘记录的是"首次生成该代码时的依据"，后来者重复提交同款不改写历史。
    """
    code_hash = compute_code_hash(code)
    structure_hash = compute_structure_hash(code)
    # 结构指纹并进 audit（复用现有 JSONB 列，免 migration）
    audit_with_struct: dict[str, Any] = dict(audit) if audit else {}
    audit_with_struct["structure_hash"] = structure_hash
    audit_json = json.dumps(audit_with_struct, default=str)

    # 先查精确 hash，再查结构指纹
    async with conn.cursor() as cur:
        await cur.execute(
            "SELECT id FROM strategy_candidates WHERE code_hash = %s",
            (code_hash,),
        )
        row = await cur.fetchone()
        if row is not None:
            return row["id"], False
        await cur.execute(
            "SELECT id FROM strategy_candidates WHERE audit->>'structure_hash' = %s LIMIT 1",
            (structure_hash,),
        )
        row = await cur.fetchone()
        if row is not None:
            return row["id"], False

    # 不存在 → 写。用 ON CONFLICT (code_hash) DO NOTHING RETURNING id 兜并发竞态：
    # check-then-insert 之间另一并发事务（live runner 回跑 + 前端手动提交同代码）可能抢先
    # INSERT，裸 INSERT 会撞 code_hash UNIQUE 抛 UniqueViolation → 上层 500 → agent 误判失败重试。
    # 改为 DO NOTHING：插入成功返新 id（created=True）；被抢则 RETURNING 空 → 查回已有行（幂等）。
    # 注：structure_hash 是软去重（无 UNIQUE 约束），其竞态最坏只多落一行同款，可接受。
    candidate_id = uuid4()
    async with conn.cursor() as cur:
        await cur.execute(
            """
            INSERT INTO strategy_candidates (
                id, code, code_hash, description, author, author_id,
                owner_account_id, audit, factor_snapshot
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (code_hash) DO NOTHING
            RETURNING id
            """,
            (
                str(candidate_id),
                code,
                code_hash,
                description,
                author,
                str(author_id) if author_id else None,
                str(owner_account_id) if owner_account_id else None,
                audit_json,
                json.dumps(factor_snapshot, default=str) if factor_snapshot else None,
            ),
        )
        inserted = await cur.fetchone()
        if inserted is not None:
            return inserted["id"], True
        # 并发竞态：同 code_hash 已被另一事务插入 → 查回已有行，当作"获取"语义返回
        await cur.execute(
            "SELECT id FROM strategy_candidates WHERE code_hash = %s",
            (code_hash,),
        )
        row = await cur.fetchone()
        if row is not None:
            return row["id"], False
    # 理论不可达（DO NOTHING 未插却又查不到）；兜底返本地 id 防 None
    return candidate_id, True


async def get_candidate(
    conn: AsyncConnection,
    candidate_id: UUID,
) -> dict[str, Any] | None:
    """按 id 取候选完整行；不存在返 None。"""
    async with conn.cursor() as cur:
        await cur.execute(
            """
            SELECT id, code, code_hash, description, author, author_id,
                   owner_account_id, status, metrics, fitness, last_backtest_run_id,
                   audit, factor_snapshot, created_at, updated_at
            FROM strategy_candidates
            WHERE id = %s
            """,
            (str(candidate_id),),
        )
        row = await cur.fetchone()
    return _row_to_dict(row) if row else None


async def list_candidates(
    conn: AsyncConnection,
    *,
    status: str | None = None,
    author_id: UUID | None = None,
    limit: int = 50,
    owner_account_id: str | None = None,
) -> list[dict[str, Any]]:
    """列候选（按 fitness DESC, created_at DESC，未跑过回测的排最后）。

    Args:
        status: 可选过滤 'candidate' / 'rejected' / 'promoted'
        author_id: 可选只看某用户创建的
        owner_account_id: 多租户——按账户过滤,填了只看本人候选,不填看全局(仅 dev)
    """
    sql = (
        "SELECT id, code, code_hash, description, author, author_id, "
        "owner_account_id, status, metrics, fitness, last_backtest_run_id, "
        "audit, factor_snapshot, created_at, updated_at "
        "FROM strategy_candidates WHERE 1=1"
    )
    params: list[Any] = []
    if owner_account_id is not None:
        sql += " AND owner_account_id = %s"
        params.append(owner_account_id)
    if status is not None:
        sql += " AND status = %s"
        params.append(status)
    if author_id is not None:
        sql += " AND author_id = %s"
        params.append(str(author_id))
    sql += " ORDER BY fitness DESC NULLS LAST, created_at DESC LIMIT %s"
    params.append(limit)

    async with conn.cursor() as cur:
        await cur.execute(sql, tuple(params))
        rows = await cur.fetchall()
    return [_row_to_dict(r) for r in rows]


async def update_after_backtest(
    conn: AsyncConnection,
    candidate_id: UUID,
    *,
    metrics: dict[str, Any],
    fitness: float,
    backtest_run_id: UUID | None,
) -> None:
    """回测跑完后回写 metrics / fitness / last_backtest_run_id。

    metrics 用 JSONB **merge**（`|| `）而非整列覆盖：回测相关 key（sharpe/validation/…）
    被新值更新，但 `update_sensitivity` 另路 merge 进来的 `sensitivity` key 得以保留
    ——否则"check_sensitivity 后再调参重测"会静默丢掉 sensitivity，promote 软检误报
    "没跑过敏感性"（CR #86 major）。历次回测明细仍去 backtest_runs 表查。
    """
    async with conn.cursor() as cur:
        await cur.execute(
            """
            UPDATE strategy_candidates
            SET metrics = COALESCE(metrics, '{}'::jsonb) || %s::jsonb,
                fitness = %s,
                last_backtest_run_id = %s,
                updated_at = NOW()
            WHERE id = %s
            """,
            (
                json.dumps(metrics, default=str),
                fitness,
                str(backtest_run_id) if backtest_run_id else None,
                str(candidate_id),
            ),
        )


async def update_sensitivity(
    conn: AsyncConnection,
    candidate_id: UUID,
    *,
    sensitivity: dict[str, Any],
) -> None:
    """敏感性摘要 **merge** 进 metrics（D-12）。

    不能走 ``update_after_backtest`` 的整体覆盖——那会把最近一次回测的
    metrics/validation 抹掉；这里只补 ``metrics.sensitivity`` 一个 key。
    """
    async with conn.cursor() as cur:
        await cur.execute(
            """
            UPDATE strategy_candidates
            SET metrics = COALESCE(metrics, '{}'::jsonb) || %s::jsonb,
                updated_at = NOW()
            WHERE id = %s
            """,
            (
                json.dumps({"sensitivity": sensitivity}, default=str),
                str(candidate_id),
            ),
        )


async def set_status(
    conn: AsyncConnection,
    candidate_id: UUID,
    status: str,
) -> None:
    """改 status。promote / reject 走这里；CHECK 约束保证只能是合法值。"""
    if status not in ("candidate", "rejected", "promoted"):
        raise ValueError(f"invalid status {status!r}")
    async with conn.cursor() as cur:
        await cur.execute(
            """
            UPDATE strategy_candidates
            SET status = %s, updated_at = NOW()
            WHERE id = %s
            """,
            (status, str(candidate_id)),
        )


async def promote_candidate(
    conn: AsyncConnection,
    candidate_id: UUID,
    *,
    reason: str,
    promoted_by: str,
    warnings: list[str] | None = None,
) -> None:
    """把 ``status='candidate'`` 的行切到 ``'promoted'`` 并把 promote 元数据并进
    ``audit.promotion`` JSONB 字段（reason / promoted_by / promoted_at ISO UTC 字符串，
    D-12 起加 ``warnings``：promote 时未过软门槛的记录——holdout 衰减 / 敏感性 cliff /
    检查缺失。soft check 只留痕不拒绝，观察期后再评估是否收紧为 hard gate）。

    端点层负责"当前 status==candidate + fitness 非空"校验——本函数不再二次 guard
    （避免读写分裂导致的 race）。一条 UPDATE 同时改 status + audit + updated_at 保证原子性。
    """
    async with conn.cursor() as cur:
        await cur.execute(
            """
            UPDATE strategy_candidates
            SET status = 'promoted',
                audit = COALESCE(audit, '{}'::jsonb) || jsonb_build_object(
                    'promotion',
                    jsonb_build_object(
                        'reason', %s::text,
                        'promoted_by', %s::text,
                        'promoted_at', to_char(
                            NOW() AT TIME ZONE 'UTC',
                            'YYYY-MM-DD"T"HH24:MI:SS"Z"'
                        ),
                        'warnings', %s::jsonb
                    )
                ),
                updated_at = NOW()
            WHERE id = %s
            """,
            (reason, promoted_by, json.dumps(warnings or []), str(candidate_id)),
        )


def _row_to_dict(row: Any) -> dict[str, Any]:
    """psycopg dict_row → 简化 dict。JSONB 已 decode；UUID 保留对象。"""
    return {
        "id": row["id"],
        "code": row["code"],
        "code_hash": row["code_hash"],
        "description": row["description"],
        "author": row["author"],
        "author_id": row["author_id"],
        "owner_account_id": row["owner_account_id"],
        "status": row["status"],
        "metrics": row["metrics"],
        "fitness": row["fitness"],
        "last_backtest_run_id": row["last_backtest_run_id"],
        "audit": row["audit"],
        "factor_snapshot": row["factor_snapshot"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }
