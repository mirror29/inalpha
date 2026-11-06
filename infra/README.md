# Inalpha 基础设施

本目录维护开发数据库、缓存和 Alembic migrations；完整自托管编排在仓库根目录。

## Docker 自托管全栈

```bash
bash scripts/selfhost.sh init
bash scripts/selfhost.sh up
bash scripts/selfhost.sh create-user --email you@example.com
```

`init` 会生成数据库、JWT、配置加密密钥，以及 Evolver credential grant 所需的
Ed25519 公私钥。生成后的 `infra/.env.selfhost` 权限为 `600`，不要提交到仓库。

完整栈包含 PostgreSQL、Redis、migration、五个 Python services（data / paper / research /
factor / evolver）、Mastra 与 Dashboard。Dashboard 只绑定宿主机 `127.0.0.1:3001`；远程访问
应由部署者的 Caddy、Nginx 或 Tunnel 提供 HTTPS，并只代理 Dashboard。用户级 LLM key、
逐用户 JWT 与 Evolver 即时凭证边界见根目录 README。

## 开发数据库与缓存

```bash
cd infra
cp .env.example .env          # 设置开发数据库密码
docker compose up -d
docker compose ps             # postgres / redis 应为 healthy
```

模板的本地密码与端口和仓库根 `.env.example` 一致；如果修改 `POSTGRES_PASSWORD` 或
`POSTGRES_PORT`，也要同步更新根 `.env` 中的 `DATABASE_URL`。

## 数据库迁移

首次安装与任何拉取新 migration 后都升级到当前 head；`scripts/dev.sh` 不会自动迁移。

生产或长期运行的自建实例应采用协调停机升级：先停止旧版应用 service，再执行 migration，
最后统一启动同一版本的 Dashboard、orchestration 与 Python services。尤其从 0041 起，新的
Evolver run 必须携带冻结 LLM 快照，旧 writer 与新 schema 不支持滚动混跑。

```bash
cd infra/migrations
uv sync
uv run alembic upgrade head
uv run alembic current
```

新增 schema：

```bash
uv run alembic revision -m "add foo column"
# 编辑 versions/<revision>_add_foo_column.py 的 upgrade() / downgrade()
uv run alembic upgrade head
```

当前 schema 除 bars/ticks 等时序数据外，还承载账户/持仓/订单、trade plans 与审批、回测和
live runner、factor/research 记录、用户与加密 LLM 配置，以及 evolution runs/candidates。
不要在文档或脚本中假定固定 migration 编号或完整表清单，以 `alembic current` 与 `\dt` 为准。

## 验证

```bash
docker compose exec postgres psql -U quant -d inalpha -c "\dx"
docker compose exec postgres psql -U quant -d inalpha -c "\dt"
docker compose exec postgres psql -U quant -d inalpha -c \
  "SELECT hypertable_name FROM timescaledb_information.hypertables"
```

TimescaleDB extension 应存在；hypertable 至少包括 bars/ticks。架构和当前阶段分别见
[`docs/01-architecture-overview.md`](../docs/01-architecture-overview.md) 与
[`docs/04-current-state.md`](../docs/04-current-state.md)。

## 清理

生产部署脚本 `scripts/deploy.sh` 需要宿主机 `python3`、`flock` 及支持 `up --wait`
的 Docker Compose。部署之间使用文件锁互斥；服务在 300 秒内通过健康检查后，才清理
Compose 中明确声明的 Inalpha 应用镜像仓库。每个仓库保留最近两个构建版本，同时保留
所有容器（包括已停止容器）引用、本次部署前引用、以及构建未满 24 小时的镜像。
失败部署不清理；清理失败会输出告警。数据库、volume、容器及其他仓库镜像不参与清理。
无仓库信息的悬空镜像会保留，需要人工确认来源后处理。

手动检查时默认只预览，确认后添加 `--apply`：

```bash
python3 scripts/cleanup-images.py --repository ghcr.io/mirror29/inalpha-dashboard
```

生产 Compose 的全部服务均配置 `json-file` 日志轮转，每个文件最多 `10m`，保留
`3` 个文件，每容器约 30 MB。新配置在容器下次创建或重建时生效，普通 restart
不会改变已有容器的日志配置。更新配置后应随计划内部署启用，不必重启 Docker daemon。

`docker compose down` 只停止容器并保留 volume。`docker compose down -v` 会永久删除开发
数据库 volume，只能在明确要重建本地数据时使用。
