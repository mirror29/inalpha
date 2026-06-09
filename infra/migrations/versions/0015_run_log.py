"""strategy_runs.error_log → run_log：统一运行日志（info/warn/error）

Revision ID: 0015
Revises: 0014
Create Date: 2026-06-08

live runner 原来只在出错时往 ``error_log`` 追加 ``{ts, error, code}``。需求升级为「记录
agent 的所有日志」——起跑 / 出单 / 停止等 info 级活动、退避 / 熔断 / TTL 等 warn 级、终态
error 级，统一进一个带 ``level`` 的运行日志。

本迁移：

1. 列改名 ``error_log`` → ``run_log``（语义从「错误日志」升级为「运行日志」）。
2. 历史条目形态 ``{ts, error, code}`` → ``{ts, level:'error', msg, code}``（旧条目都是错误，
   补 ``level='error'``，``error`` 字段更名 ``msg``，与新写入形态统一）。

容量：写入侧（``storage.append_log``）按滚动窗口裁到最近 N 条，防 info 级随 bar 无界膨胀。
"""
from __future__ import annotations

from alembic import op

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE strategy_runs RENAME COLUMN error_log TO run_log")
    # 历史条目 {ts, error, code} → {ts, level:'error', msg, code}
    op.execute(
        """
        UPDATE strategy_runs
        SET run_log = COALESCE(
            (
                SELECT jsonb_agg(
                    jsonb_build_object(
                        'ts', e->>'ts',
                        'level', 'error',
                        'msg', e->>'error',
                        'code', e->'code'
                    )
                )
                FROM jsonb_array_elements(run_log) AS e
            ),
            '[]'::jsonb
        )
        WHERE jsonb_array_length(run_log) > 0
        """
    )


def downgrade() -> None:
    # 回退条目形态 {ts, level, msg, code} → {ts, error, code}（丢弃 level；msg→error）。
    # **只保留 level='error'**：旧 error_log 语义是纯错误日志，旧版 RunnerCard 角标 =
    # error_log.length（不分级）、面板把每条都当错误显示。若把 info/warn（起跑 / 出单 /
    # 停止，随 bar 累积）一并写回，回滚后角标暴增、正常活动被当错误 —— 在排障窗口反成噪音。
    # 保留 WITH ORDINALITY + ORDER BY ord 维持时序。
    op.execute(
        """
        UPDATE strategy_runs
        SET run_log = COALESCE(
            (
                SELECT jsonb_agg(
                    jsonb_build_object('ts', e->>'ts', 'error', e->>'msg', 'code', e->'code')
                    ORDER BY ord
                )
                FROM jsonb_array_elements(run_log) WITH ORDINALITY AS arr(e, ord)
                WHERE e->>'level' = 'error'
            ),
            '[]'::jsonb
        )
        WHERE jsonb_array_length(run_log) > 0
        """
    )
    op.execute("ALTER TABLE strategy_runs RENAME COLUMN run_log TO error_log")
