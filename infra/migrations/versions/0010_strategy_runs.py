"""paper_strategy_runs —— live runner 状态表（D-11 issue #1）

Revision ID: 0010
Revises: 0009
Create Date: 2026-06-02

D-9 候选池 + promote 闭环已通，但 ``status='promoted'`` 只是状态切换，没人按行情
tick 策略的 ``on_bar``。本表记录"哪个 promoted candidate 正在哪个市场按什么周期
自动跑"的 live 状态，由 ``live_runner.LiveRunnerManager`` 的后台 asyncio task 驱动。

设计：

- ``candidate_id``：跑的是哪个 promoted 候选（FK → strategy_candidates.id，
  ON DELETE CASCADE：删候选连带删其 run，不留孤儿）
- ``account_id``：发起用户的账户——持仓 / 风控按此账户隔离（与手动下单同账户）。
  **不**加 FK：账户是首单 lazy create（get_or_create），run 在下首单前就 insert，
  加 FK 会让 run 插入时撞不存在的账户
- ``venue`` / ``symbol`` / ``timeframe`` / ``params``：candidate 表不含这些，start 时传
- ``last_bar_ts``：已处理到的最新 bar（严格单调，去重防重复喂同一根）
- ``cumulative_pnl``：累计已实现盈亏（展示用）
- ``error_log``：JSONB 数组，每条 {ts, error}；连续错 N 次置 errored
- ``UNIQUE(candidate_id) WHERE status='running'``：同 candidate 同时只一个 running
"""
from __future__ import annotations

from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE strategy_runs (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            candidate_id    UUID NOT NULL
                REFERENCES strategy_candidates(id) ON DELETE CASCADE,
            account_id      UUID NOT NULL,
            status          TEXT NOT NULL
                CHECK (status IN ('running', 'stopped', 'errored')),
            venue           TEXT NOT NULL,
            symbol          TEXT NOT NULL,
            timeframe       TEXT NOT NULL,
            params          JSONB NOT NULL DEFAULT '{}'::jsonb,
            last_bar_ts     TIMESTAMPTZ,
            cumulative_pnl  NUMERIC NOT NULL DEFAULT 0,
            error_log       JSONB NOT NULL DEFAULT '[]'::jsonb,
            started_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            stopped_at      TIMESTAMPTZ
        )
        """
    )
    # 同 candidate 同时只能有一个 running（部分唯一索引）
    op.execute(
        "CREATE UNIQUE INDEX strategy_runs_one_running_per_candidate "
        "ON strategy_runs (candidate_id) WHERE status = 'running'"
    )
    # 按账户列表 + 状态过滤
    op.execute(
        "CREATE INDEX strategy_runs_account_idx "
        "ON strategy_runs (account_id, started_at DESC)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS strategy_runs_account_idx")
    op.execute("DROP INDEX IF EXISTS strategy_runs_one_running_per_candidate")
    op.execute("DROP TABLE IF EXISTS strategy_runs")
