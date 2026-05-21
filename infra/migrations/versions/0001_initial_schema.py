"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-05-21

第一份 migration —— 建立 MVP 所需的全部表。

包含：
- 启用 timescaledb / pgcrypto 扩展
- 时序表：bars（K 线）+ ticks（quote tick），转 hypertable
- ticks 7 天后自动压缩
- 业务表：strategies / backtest_runs / strategy_instances / orders /
  research_memory

不包含（等需要再加）：
- orderbook_snapshots —— Phase F 接 L2 数据时再加
- users / sessions —— Next.js + better-auth 自己管，本 migration 不碰
- Mastra mastra_* 表 —— PostgresStore.init() 启动时自动建
"""
from __future__ import annotations

from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None


def upgrade() -> None:
    # ============ 启用扩展 ============
    op.execute("CREATE EXTENSION IF NOT EXISTS timescaledb")
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")  # gen_random_uuid()

    # ============ 时序：K 线（bars） ============
    op.execute(
        """
        CREATE TABLE bars (
            ts          TIMESTAMPTZ NOT NULL,
            venue       TEXT NOT NULL,
            symbol      TEXT NOT NULL,
            timeframe   TEXT NOT NULL,
            open        NUMERIC NOT NULL,
            high        NUMERIC NOT NULL,
            low         NUMERIC NOT NULL,
            close       NUMERIC NOT NULL,
            volume      NUMERIC NOT NULL,
            PRIMARY KEY (ts, venue, symbol, timeframe)
        )
        """
    )
    op.execute(
        """
        SELECT create_hypertable(
            'bars', 'ts',
            chunk_time_interval => INTERVAL '7 days',
            if_not_exists => TRUE
        )
        """
    )
    op.execute(
        "CREATE INDEX bars_venue_symbol_tf_ts_desc_idx "
        "ON bars (venue, symbol, timeframe, ts DESC)"
    )

    # ============ 时序：quote tick（ticks） ============
    op.execute(
        """
        CREATE TABLE ticks (
            ts_event    TIMESTAMPTZ NOT NULL,
            ts_init     TIMESTAMPTZ NOT NULL,
            venue       TEXT NOT NULL,
            symbol      TEXT NOT NULL,
            bid_price   NUMERIC,
            ask_price   NUMERIC,
            bid_size    NUMERIC,
            ask_size    NUMERIC,
            PRIMARY KEY (ts_event, venue, symbol)
        )
        """
    )
    op.execute(
        """
        SELECT create_hypertable(
            'ticks', 'ts_event',
            chunk_time_interval => INTERVAL '1 day',
            if_not_exists => TRUE
        )
        """
    )
    op.execute(
        """
        ALTER TABLE ticks SET (
            timescaledb.compress,
            timescaledb.compress_segmentby = 'venue,symbol'
        )
        """
    )
    op.execute(
        "SELECT add_compression_policy('ticks', INTERVAL '7 days', if_not_exists => TRUE)"
    )

    # ============ 业务：strategies（策略定义） ============
    op.execute(
        """
        CREATE TABLE strategies (
            id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            name        TEXT NOT NULL,
            code_ref    TEXT NOT NULL,
            params      JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            created_by  UUID
        )
        """
    )
    op.execute("CREATE UNIQUE INDEX strategies_name_uidx ON strategies (name)")

    # ============ 业务：backtest_runs ============
    op.execute(
        """
        CREATE TABLE backtest_runs (
            id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            strategy_id   UUID NOT NULL REFERENCES strategies(id) ON DELETE CASCADE,
            config        JSONB NOT NULL,
            status        TEXT NOT NULL
                CHECK (status IN ('pending', 'running', 'done', 'failed', 'canceled')),
            metrics       JSONB,
            error         TEXT,
            started_at    TIMESTAMPTZ,
            finished_at   TIMESTAMPTZ,
            created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            created_by    UUID
        )
        """
    )
    op.execute(
        "CREATE INDEX backtest_runs_strategy_created_idx "
        "ON backtest_runs (strategy_id, created_at DESC)"
    )
    op.execute(
        "CREATE INDEX backtest_runs_active_idx "
        "ON backtest_runs (status) WHERE status IN ('pending', 'running')"
    )

    # ============ 业务：strategy_instances（实盘/模拟盘活跃实例） ============
    op.execute(
        """
        CREATE TABLE strategy_instances (
            id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            strategy_id   UUID NOT NULL REFERENCES strategies(id) ON DELETE RESTRICT,
            mode          TEXT NOT NULL CHECK (mode IN ('paper', 'live')),
            venue         TEXT NOT NULL,
            status        TEXT NOT NULL
                CHECK (status IN ('starting', 'running', 'stopped', 'error')),
            params        JSONB NOT NULL DEFAULT '{}'::jsonb,
            error         TEXT,
            started_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            stopped_at    TIMESTAMPTZ,
            created_by    UUID
        )
        """
    )
    op.execute(
        "CREATE INDEX strategy_instances_active_idx "
        "ON strategy_instances (status) WHERE status IN ('starting', 'running')"
    )
    op.execute(
        "CREATE INDEX strategy_instances_strategy_started_idx "
        "ON strategy_instances (strategy_id, started_at DESC)"
    )

    # ============ 业务：orders ============
    op.execute(
        """
        CREATE TABLE orders (
            client_order_id       TEXT PRIMARY KEY,
            venue_order_id        TEXT,
            strategy_instance_id  UUID REFERENCES strategy_instances(id) ON DELETE SET NULL,
            instrument_id         TEXT NOT NULL,
            side                  TEXT NOT NULL CHECK (side IN ('BUY', 'SELL')),
            type                  TEXT NOT NULL
                CHECK (type IN ('MARKET', 'LIMIT', 'STOP_MARKET', 'STOP_LIMIT')),
            quantity              NUMERIC NOT NULL,
            price                 NUMERIC,
            status                TEXT NOT NULL CHECK (status IN (
                'NEW', 'SUBMITTED', 'ACCEPTED', 'PARTIALLY_FILLED',
                'FILLED', 'CANCELED', 'REJECTED', 'EXPIRED'
            )),
            filled_quantity       NUMERIC NOT NULL DEFAULT 0,
            avg_fill_price        NUMERIC,
            ts_event              TIMESTAMPTZ NOT NULL,
            ts_init               TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    # venue_order_id 唯一但允许为 NULL（交易所还没分配前）
    op.execute(
        "CREATE UNIQUE INDEX orders_venue_order_id_uidx "
        "ON orders (venue_order_id) WHERE venue_order_id IS NOT NULL"
    )
    op.execute(
        "CREATE INDEX orders_strategy_ts_init_idx "
        "ON orders (strategy_instance_id, ts_init DESC)"
    )
    op.execute(
        "CREATE INDEX orders_active_idx ON orders (status) "
        "WHERE status NOT IN ('FILLED', 'CANCELED', 'REJECTED', 'EXPIRED')"
    )

    # ============ 业务：research_memory（TradingMemoryLog 落库版） ============
    op.execute(
        """
        CREATE TABLE research_memory (
            id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id        UUID,
            ticker         TEXT NOT NULL,
            decision_date  DATE NOT NULL,
            rating         TEXT CHECK (rating IS NULL OR rating IN (
                'Buy', 'Overweight', 'Hold', 'Underweight', 'Sell'
            )),
            decision       TEXT NOT NULL,
            reflection     TEXT,
            outcome        JSONB,
            status         TEXT NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending', 'resolved')),
            created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            resolved_at    TIMESTAMPTZ
        )
        """
    )
    op.execute(
        "CREATE INDEX research_memory_user_ticker_date_idx "
        "ON research_memory (user_id, ticker, decision_date DESC)"
    )
    op.execute(
        "CREATE INDEX research_memory_pending_idx "
        "ON research_memory (user_id, created_at DESC) WHERE status = 'pending'"
    )


def downgrade() -> None:
    # 倒序删，先删带 FK 的子表
    op.execute("DROP TABLE IF EXISTS research_memory")
    op.execute("DROP TABLE IF EXISTS orders")
    op.execute("DROP TABLE IF EXISTS strategy_instances")
    op.execute("DROP TABLE IF EXISTS backtest_runs")
    op.execute("DROP TABLE IF EXISTS strategies")
    # 时序表（带 hypertable 元数据，drop 会自动清理）
    op.execute("DROP TABLE IF EXISTS ticks")
    op.execute("DROP TABLE IF EXISTS bars")
    # 不 drop 扩展（可能被同库其他东西用着）
