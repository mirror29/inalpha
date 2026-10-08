# Inalpha Data · 多市场数据服务

`services/data` 是 FastAPI 数据接入层（`:8001`）：统一查询与缓存行情、财报、新闻、市场
概览、成分股、汇率和永续合约数据，并把 bars/ticks 写入 PostgreSQL + TimescaleDB。

## 当前能力

- **多市场路由**：Binance / CCXT（crypto）、akshare / BaoStock（A股、港股）、
  yfinance / Alpaca（美股与全球单股、指数）、FRED（宏观）。
- **行情与时序**：`/bars`、`/ticker`、`/backfill/bars`、`/symbols/search`，支持 freshness
  与本地缓存策略。
- **基本面与市场情报**：`/fundamentals`、`/news`、`/market/*`、`/constituents`。
- **Web 与跨资产辅助数据**：`/web/search`、`/web/news`、`/web/fetch`、`/fx`、
  `/perp/funding`。
- 除 `/health` 外的业务端点均要求用户 JWT；连接器失败按稳定错误码返回，不静默伪造数据。

关键目录：

| 目录 | 职责 |
|---|---|
| `connectors/` | 市场、新闻、搜索与基本面连接器 |
| `api/` | HTTP 路由、输入校验与错误映射 |
| `storage/` | bars 与指数成分等持久化 |
| `venues.py` | venue / symbol 能力与市场路由 |
| `scheduler.py` | 数据侧周期任务 |

## 本地开发

先按根 README 启动 `infra` Compose 并执行 Alembic migration，然后：

```bash
cd services/data
uv sync --group dev
uv run uvicorn inalpha_data.main:app --reload --port 8001

uv run ruff check .
uv run pytest
```

配置统一从仓库根 `.env` 读取。`DATABASE_URL` 与 `JWT_SECRET` 必填；Binance 公共行情无需
交易 key，FRED 宏观因子需要 `FRED_API_KEY`，其余付费或认证连接器只在配置存在时启用。

## 事件持续采集

`EVENT_ARCHIVE_ENABLED=true` 时，归档任务按 `EVENT_ARCHIVE_INTERVAL_S` 轮询 CoinDesk、
Kraken Blog、Bitcoin Core 官方公告和 Cointelegraph RSS。每个来源独立保留最多 50 条，
避免高频新闻挤掉低频公告。跨批次同链接仍优先保留官方来源并合并 `alternative_sources`，
不会重新施加全局条数上限；新增两源仅用于归档，不改变公共 `/news` 的来源集合、全局上限与去重。

日志 `event_archive_source_fetched` 给出来源、状态、返回数及过滤后条目数；
某一来源写入失败会回滚该批事务并记录 `event_archive_source_failed`，继续写入其他来源；
服务取消仍立即传播。`event_archive_source_completed` 表示该批条目已完成幂等写入，**不表示新增条目数**。
错误日志保留异常类型与最近六个代码位置（文件名、函数、行号），不记录异常原文、
源码行、绝对路径、原始响应或凭据。抽取继续通过持久化 outbox 和正常 Research
worker 执行。不同来源可能报道同一事件，原文数量不能直接作为独立事件数量。

多 HTTP worker 通过独立 PostgreSQL session advisory lock 连接协调后台采集。采集者在
轮询间隔也持锁，避免错开启动的 worker 重复轮询；取消或故障时关闭连接释放锁，其他 worker
可接手。每轮先检查持锁连接，并以采集间隔作为整轮期限；锁查询最多等待 5 秒。连接不占用
写入池，用户请求处理 worker 数不变，来源写入仍各自使用短事务。

这些 RSS 属于 `snapshot_only`。旧公告在首次采到时才进入可见集合，发布日期不能回溯
`accepted_at`，也不能补齐旧验收窗口。新增来源的未来覆盖需要另行观察，既有验收口径不随
来源扩展静默改变。真实验收与下一步统一见 [当前状态](../../docs/04-current-state.md#未完成--下一步)。
