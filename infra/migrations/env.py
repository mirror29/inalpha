"""Alembic 环境配置。

从 `infra/.env` 读 `DATABASE_URL`（不直接读 alembic.ini 里的占位），保证迁移命令
和容器配置共用同一个连接串。
"""
from __future__ import annotations

import os
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from dotenv import load_dotenv
from sqlalchemy import engine_from_config, pool

# 加载 infra/.env（不管 cwd 在哪里都能找到）
_ENV_FILE = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(_ENV_FILE)

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# 用环境变量覆盖 sqlalchemy.url（必须存在，否则跑不下去）
database_url = os.environ.get("DATABASE_URL")
if not database_url:
    raise RuntimeError(
        f"DATABASE_URL 未配置。请确认 {_ENV_FILE} 存在且写了 DATABASE_URL。"
    )
config.set_main_option("sqlalchemy.url", database_url)

# 暂时不接 SQLAlchemy ORM models，autogenerate 不可用，纯手写 op.execute
target_metadata = None


def run_migrations_offline() -> None:
    """离线模式：只生成 SQL 不连库。"""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """在线模式：连库执行。"""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
