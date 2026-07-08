"""bars 表启用 TimescaleDB 自动压缩 —— 30 天前的 bars 自动压缩。

Revision ID: 0030
Revises: 0029
Create Date: 2026-07-08

bars 是高频 K 线 hypertable（7 天 chunk），数据增长快，需要压缩降成本。
与 ticks 不同，bars 查询通常按时间倒序，因此额外加了 compress_orderby = 'ts DESC'。
只压缩不清理（无 retention_policy），历史数据保留完整。
"""
from __future__ import annotations

from alembic import op

revision: str = "0030"
down_revision: str | None = "0029"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE bars SET (
            timescaledb.compress,
            timescaledb.compress_segmentby = 'venue, symbol, timeframe',
            timescaledb.compress_orderby = 'ts DESC'
        )
        """
    )
    op.execute(
        "SELECT add_compression_policy('bars', INTERVAL '30 days', if_not_exists => TRUE)"
    )


def downgrade() -> None:
    op.execute(
        "SELECT remove_compression_policy('bars', if_exists => TRUE)"
    )
    op.execute(
        """
        ALTER TABLE bars SET (
            timescaledb.compress = false
        )
        """
    )
    op.execute(
        "SELECT decompress_chunk(c) FROM show_chunks('bars') AS c"
    )
