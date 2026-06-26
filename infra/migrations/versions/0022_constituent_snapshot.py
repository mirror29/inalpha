"""constituent_snapshot —— 指数/板块成分 PIT 快照（#106 / ADR-0053 阶段 C）

Revision ID: 0022
Revises: 0021
Create Date: 2026-06-26

横截面选股/轮动回测的存活者偏差前提:每期用 as_of 那刻的**真实成分**,而非"今天还在的票"
回看。数据源现实(实测):免费**历史** PIT 成分拿不到(akshare 只回当前成分、中证 XLS 每日
覆盖),唯一免费路径 = **从今天起每日快照当前成分、向前累积**。

本表存"某 index_code 在 as_of_date 这天的成分全量"。time-travel 查询取 `as_of_date <= 目标`
的最近一份快照;早于最早快照 → 空 + 显式 non-PIT 降级(不静默假装,§3.1)。

⚠️ 与并行 perp 分支的 ``0022_perp_trading_mode`` 撞号(都基于 main 的 0021)——两分支
合 main 时后合者需 renumber 到 0023 并改 down_revision。
"""
from __future__ import annotations

from alembic import op

revision: str = "0022"
down_revision: str | None = "0021"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE constituent_snapshot (
            id               BIGSERIAL PRIMARY KEY,
            index_code       TEXT NOT NULL,
            constituent_code TEXT NOT NULL,
            name             TEXT,
            weight           NUMERIC,
            as_of_date       DATE NOT NULL,
            created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (index_code, constituent_code, as_of_date)
        )
        """
    )
    # time-travel 查询热路径:给定 index_code 找 as_of_date <= 目标 的最近一份
    op.execute(
        "CREATE INDEX idx_constituent_snapshot_lookup"
        " ON constituent_snapshot (index_code, as_of_date DESC)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS constituent_snapshot")
