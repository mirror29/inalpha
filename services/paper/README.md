# Inalpha Paper · 回测、模拟盘与交易护栏

`services/paper` 是事件驱动量化内核（`:8002`）。回测与 live runner 共用 Strategy / Clock /
MessageBus / execution 模型；账户、订单、持仓、交易计划、候选、回测与运行记录统一写入
PostgreSQL。当前只做模拟执行，不连接真钱经纪商。

## 当前能力

- **回测与稳健性**：单次回测、buy-and-hold baseline、时序 CV、参数邻域敏感性、多市场
  annualization、交易与 fitness 明细持久化。
- **策略生命周期**：内置策略、research hint compose、LLM authored strategy 三道沙盒、
  candidate leaderboard 与显式 promote。
- **Live runner**：只有 promoted 且 owner 匹配的候选可启动；按新 bar 自动运行，保存逐 bar
  决策，支持运行 TTL、单账户上限、错误分类与退避。
- **Plan/Exec 下单护栏**：`/plans` create / approve / execute，审批 token 一次性且短 TTL；
  `/orders/submit` 仍经过机器 RiskGuard，LLM 没有可绕过 plan 的直下单 tool。
- **多市场账户与风控**：跨币种 cash / FX、spot/perp 约束、交易时段日历、cooldown、
  low-profit、max-drawdown、stop-loss 与 risk lock 审计。

## 代表性 API

| 路径 | 用途 |
|---|---|
| `POST /backtest` · `/backtest/cv` · `/backtest/sensitivity` | 回测与稳健性评估 |
| `POST /strategy_candidates` · `GET /strategy_candidates/*` | 创作、查询与审计候选 |
| `POST /strategy_runs` · `/strategy_runs/{id}/stop` | 启停 live runner |
| `GET /strategy_runs/{id}/decisions` | 回放逐 bar 决策 |
| `POST /plans` · `/plans/{id}/approve` · `/plans/{id}/execute` | 交易计划与一次性审批 |
| `GET /accounts/me` · `/positions` · `/orders` | 账户、持仓与订单 |
| `GET /risk/rules` · `/risk/locks` | 风控配置与锁记录 |

除 `/health` 与登录入口外，领域 API 都按认证 owner 隔离。核心实现分布在 `engine/`、
`execution/`、`strategy_authoring/`、`storage/` 与 `api/`。

## 本地开发

先按根 README 启动开发数据库并把 migration 升到 head，然后：

```bash
cd services/paper
uv sync --group dev
uv run uvicorn inalpha_paper.main:app --reload --port 8002

uv run ruff check .
uv run pytest
```

完整交易信任边界与当前阶段见 [`docs/04-current-state.md`](../../docs/04-current-state.md)。

## Isolated live run wallets

New live runs transfer main-account capital into independent wallets; manual trades and historical positions stay in the main book. Stopping does not close positions or release capital. Use the stopped, flat run's `POST /strategy_runs/{id}/release_capital` endpoint to settle once. Legacy shared-book statistics are explicitly unverified or contaminated. See [accounting and rollout](../../docs/05-paper-wallet-accounting.md).
