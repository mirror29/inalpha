# infra

容器与数据库的基础设施。

## 起服务

```bash
cd infra
cp .env.example .env          # 改 POSTGRES_PASSWORD
docker compose up -d
docker compose ps             # 应看到 postgres / redis 都 healthy
```

## 跑数据库迁移

第一次：

```bash
cd infra/migrations
uv sync                       # 创建 .venv，安装 alembic + psycopg
uv run alembic upgrade head   # 应用 0001_initial_schema
```

后续新增表 / 改字段：

```bash
cd infra/migrations
uv run alembic revision -m "add foo column"   # 生成新 version 文件
# 编辑 versions/<rev>_add_foo_column.py 的 upgrade() / downgrade()
uv run alembic upgrade head
```

## 验证

```bash
# 进 postgres 看 timescaledb 装好了没
docker compose exec postgres psql -U quant -d Inalpha -c "\dx"
# 应有 timescaledb 行

# 看表都建了没
docker compose exec postgres psql -U quant -d Inalpha -c "\dt"
# 应看到 bars / ticks / strategies / backtest_runs / strategy_instances /
#       orders / research_memory + alembic_version

# 看时序表是不是 hypertable
docker compose exec postgres psql -U quant -d Inalpha -c \
  "SELECT hypertable_name FROM timescaledb_information.hypertables"
# 应有 bars / ticks
```

## 清理（小心，会删数据）

```bash
docker compose down            # 停容器但保留数据 volume
docker compose down -v         # 连数据 volume 一起删（开发期重置可用）
```

## 参考

- 表结构详细说明：`docs/decisions/0003-timeseries-db.md`
- 整体架构：`docs/03-kernel-design.md`
