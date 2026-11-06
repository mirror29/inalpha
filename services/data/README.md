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
- 除 `/health` 外的业务端点均要求鉴权（事件归档/抽取端点使用限定用途的服务身份，其他业务使用用户 JWT）；连接器失败按稳定错误码返回，不静默伪造数据。

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

## 历史事件纠正（已部署，单条生产写入与显式重发已验证）

`POST /events/facts/{fact_id}/corrections` 沿用 `event_extract` 服务鉴权，普通用户 JWT
不能修改平台事实。请求绑定纠正 UUID、原事实哈希、审核理由与 v3 分类结果；仅接受活动的
v2 监管事实，保留资产、行动、证据与有效时间，不接收客户端可见时间或事实键。

迁移 `0069` 保存来源、撤回版本、替代版本及已验证服务身份。跨分类纠正原子撤回旧规范键、
追加规范替代键；同类确认追加一个版本。可见时间来自锁内数据库时钟，旧版本及冻结快照
保留。重复请求返回同一审计结果，输入或身份变化、来源已有新版本、替代分类已存在时拒绝；
已有替代分类需显式审核撤回旧事实，不能覆盖替代事实。存在审计记录时迁移拒绝降级。
`source_fact_id` 是保留的原始不可变行；`retired_fact_id` 是同一逻辑键下新追加的撤回版本ID，
不是把原始行改成撤回。二者都是事实行UUID；纠正后的活动列表不显示撤回版本。

这不是自动历史重抽取：Research 默认预览/摘要绑定执行的审核入口已有代码和自动化，完整生产逐项审核尚待验收，不会追溯补齐
旧实验窗口。局部验收与下一步见 [当前状态](../../docs/04-current-state.md)。

2026-10-14初次部署回执：Data/Research已固定部署`9fdb8b8a`，schema0069；未登录读取/纠正401，
审计仍0。真实预览未写入，不等于生产纠正通过。见 [发布回执](../../docs/validation/event-fact-review-deployment-2026-10-14.json)。

后续已核实并纠正一条Kraken科普误分类，第二个独立Research helper进程显式重发返回同一审计/替代ID，未重复写入；原摘要与纠正前可见性保留。不是认证Research入站HTTP或生产服务重启验收。见 [单条生产回执](../../docs/validation/event-fact-single-production-2026-10-14.json)。
