"""scheduler_jobs + scheduler_runs 表（D-9 · 类 Hermes 定时 agent 模式）

Revision ID: 0004
Revises: 0003
Create Date: 2026-05-22

D-9 需求：让 Inalpha 支持类 Hermes 定时模式，扩展场景如：
- 每日盘前 deep_dive
- 周期性 backfill 行情
- 后续：定时复盘、定时重训因子

本 migration 加两张表：
1. scheduler_jobs —— 任务定义（cron 表达式 / 时区 / mode / payload）
2. scheduler_runs —— 执行历史（每次触发一行，含状态、结果、错误）

并 INSERT 两条种子任务（enabled=false，需用户手动开启）作为 MVP：
- daily_btc_deep_dive：每日 08:00 (Asia/Shanghai) agent mode 调 orchestrator
- hourly_btc_backfill：每小时 5 分 tool mode 调 data.backfill_bars
"""
from __future__ import annotations

from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None


def upgrade() -> None:
    # ============ scheduler_jobs ============
    op.execute(
        """
        CREATE TABLE scheduler_jobs (
            job_id        TEXT PRIMARY KEY,
            cron_expr     TEXT NOT NULL,
            timezone      TEXT NOT NULL DEFAULT 'UTC',
            mode          TEXT NOT NULL CHECK (mode IN ('tool', 'agent')),
            payload       JSONB NOT NULL,
            enabled       BOOLEAN NOT NULL DEFAULT TRUE,
            description   TEXT,
            created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )

    # ============ scheduler_runs ============
    op.execute(
        """
        CREATE TABLE scheduler_runs (
            run_id        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            job_id        TEXT NOT NULL REFERENCES scheduler_jobs(job_id) ON DELETE CASCADE,
            scheduled_at  TIMESTAMPTZ NOT NULL,
            started_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            finished_at   TIMESTAMPTZ,
            status        TEXT NOT NULL CHECK (status IN ('running', 'success', 'failed', 'timeout')),
            trigger       TEXT NOT NULL DEFAULT 'cron' CHECK (trigger IN ('cron', 'manual')),
            result        JSONB,
            error         JSONB
        )
        """
    )
    op.execute(
        "CREATE INDEX scheduler_runs_job_started_idx "
        "ON scheduler_runs (job_id, started_at DESC)"
    )
    op.execute(
        "CREATE INDEX scheduler_runs_running_idx "
        "ON scheduler_runs (job_id) WHERE status = 'running'"
    )

    # ============ 种子任务（enabled=false，需用户手动开启） ============
    op.execute(
        """
        INSERT INTO scheduler_jobs (job_id, cron_expr, timezone, mode, payload, enabled, description)
        VALUES (
            'daily_btc_deep_dive',
            '0 8 * * *',
            'Asia/Shanghai',
            'agent',
            '{"agent": "orchestrator", "prompt": "对 BTC/USDT 做 deep_dive（lookback 30d, timeframe 1h），输出 ResearchPlan 摘要并写入 audit log。不要下单。"}'::jsonb,
            FALSE,
            'D-9 种子：每日 08:00 BTC 盘前研究'
        )
        """
    )
    op.execute(
        """
        INSERT INTO scheduler_jobs (job_id, cron_expr, timezone, mode, payload, enabled, description)
        VALUES (
            'hourly_btc_backfill',
            '5 * * * *',
            'UTC',
            'tool',
            '{"tool": "data.backfill_bars", "input": {"venue": "binance", "symbol": "BTC/USDT", "timeframe": "1h"}}'::jsonb,
            FALSE,
            'D-9 种子：每小时 BTC 1h K 线增量'
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS scheduler_runs_running_idx")
    op.execute("DROP INDEX IF EXISTS scheduler_runs_job_started_idx")
    op.execute("DROP TABLE IF EXISTS scheduler_runs")
    op.execute("DROP TABLE IF EXISTS scheduler_jobs")
