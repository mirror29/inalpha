# 03 · Inalpha 内核架构（正式版）

> 状态：**Phase C 正式设计**，基于 Phase B 4 份 repo 拆解结论 + 用户锁定决策。
> 取代 `01-architecture-overview.md` 中的 high-level 草图；01 保留作快照。

## 锁定决策（Phase C·2026-05-21）

| 维度 | 决策 |
|---|---|
| 内核语言 | **MVP 纯 Python**，后期 Rust 优化 hot path |
| MVP 范围 | **Crypto（Binance 起步）+ TradingAgents 风格多 agent 研究** |
| 时序数据库 | **Postgres + TimescaleDB**（与 Mastra PostgresStore 同台） |
| 跨服务通信 | **HTTP REST + WebSocket** 组合 |
| 编排框架 | Mastra（TypeScript） |
| 多 agent | Mastra supervisor pattern，**不嵌 LangGraph** |
| Swarm worker 池 | 在各 engine 服务内部（RQ → Celery） |

---

## MVP 范围（Phase E 目标）

**端到端能跑通的最小闭环**：

```
用户对话："帮我研究 BTC 这周，把建议放进模拟盘"
     ↓
Mastra Orchestrator（supervisor agent）
     ↓
research workflow：foreach analyst（fundamentals/sentiment/news/technical）
                  → bull vs bear 辩论
                  → research manager 输出 plan
     ↓
trader agent：把 plan 翻译成 SubmitOrderIntent
     ↓
risk 工程规则：仓位 / 单笔金额 / 速率检查
     ↓
paper-engine（同代码 = backtest=live）下模拟单
     ↓
对话返回：决策 + 模拟成交回执 + 后续监控告知
```

**MVP 包含**：

- ✅ Crypto 数据接入（Binance via CCXT，REST + WebSocket）
- ✅ 时序数据写入 TimescaleDB（K 线、Tick、订单簿快照）
- ✅ Python 内核：Clock / MessageBus / Strategy / Gateway / Engine（事件驱动，参考 Nautilus）
- ✅ 回测 = 模拟盘 = 实盘**同代码路径**（Clock 切换）
- ✅ 简单规则化策略（如 SMA Cross / 网格）作为 Strategy 基类验证
- ✅ Mastra：1 Orchestrator + 4 Analyst + 1 Risk Agent + 1 Trader Agent
- ✅ Research-service（FastAPI）暴露 `/research/deep_dive` 给 Mastra tool
- ✅ Paper-engine（FastAPI）暴露 `/strategy/start` / `/positions` 给 Mastra tool
- ✅ Next.js + CopilotKit 对话 UI

**MVP 不包含**（Phase F 之后）：

- ❌ 实盘下单到真实资金账户
- ❌ ML 因子 pipeline（qlib 集成留给 factor-service）
- ❌ A 股 / 美股 / 国内期货 / 外汇
- ❌ L2 order book replay 高保真撮合（先 L1）
- ❌ Swarm 跑批回测（仅做单 strategy）
- ❌ Mastra workflow 长任务 suspend-resume

### D-8a 已完成项（2026-05-21）

> 详细模块清单与代码入口见 [`docs/04-current-state.md`](./04-current-state.md)。

- ✅ `services/data`：CCXT Binance + Postgres / TimescaleDB
- ✅ `services/paper`：内核 + 3 策略（`buy_and_hold` / `sma_cross` / `mean_reversion`）+ `POST /orders/submit` 单笔下单端点 + `RiskEngine` 基础
- ✅ `packages/orchestration`：
  - 三 agent（`orchestrator` / `trader` / `risk`）拆分（Mastra supervisor 模式）
  - Hooks runner（`PreToolUse` / `PostToolUse` / `PostToolUseFailure` / `SessionStart` / Stop）
  - Permission Engine（allow / ask / deny 三态 + 参数 predicate）
  - Plan/Exec 三 tool（`createTradePlan` / `approveTradePlan` / `executeTradePlan`）+ Plan Store（in-memory，含 `approval_token` 派发）

**D-8b / D-9 在做**：`trade_plans` / `approval_tokens` Postgres 表 + Alembic migration；
RiskEngine 规则化（max notional / 价格偏离 / 日损上限）+ paper-service 真接入。

---

## 三层架构

```
┌────────────────────────────────────────────────────────────────┐
│              apps/web  (Next.js 16 + CopilotKit)                │
│   浏览器对话 UI / 认证 / 流式响应 / 用户偏好                       │
│   挂在同一 Next.js 进程的 API Route 下                            │
└─────────────────────────────┬──────────────────────────────────┘
                              │ same-origin
                              ▼
┌────────────────────────────────────────────────────────────────┐
│           packages/orchestration  (Mastra · TypeScript)         │
│   agents/  orchestrator + trader + risk + research_hub          │
│   workflows/  deep_research / swarm_backtest / strategy_lifecycle│
│   tools/  data_* / research_* / paper_* / live_* / factor_*     │
│   memory/  Mastra Memory + PostgresStore                        │
└─────┬──────────────┬────────────────┬───────────────┬───────────┘
      │ HTTP + WS    │ HTTP + WS      │ HTTP + WS     │ HTTP + WS
      ▼              ▼                ▼               ▼
┌─────────────┐ ┌──────────────┐ ┌─────────────┐ ┌──────────────┐
│   services/ │ │  services/   │ │ services/   │ │ services/    │
│  data       │ │  research    │ │ paper       │ │ factor       │
│ (FastAPI)   │ │ (FastAPI)    │ │ (FastAPI)   │ │ (FastAPI)    │
│             │ │              │ │ + kernel    │ │ + qlib       │
│ CCXT/yfin   │ │ multi-agent  │ │ Clock/Bus/  │ │ Pipeline /   │
│ /tushare    │ │ debate       │ │ Strategy/   │ │ Alpha158 /   │
│             │ │ (代理调 LLM) │ │ Gateway     │ │ ML factors   │
└──────┬──────┘ └──────┬───────┘ └─────┬───────┘ └──────┬───────┘
       │               │                │                 │
       └───────┬───────┴────────────────┴─────────────────┘
               ▼
       ┌──────────────────────────────────────────────┐
       │   Postgres 17 + TimescaleDB 插件              │
       │   - timescale hypertable: bars / ticks /     │
       │     orderbook_snapshots                       │
       │   - regular tables: accounts / strategies /  │
       │     orders / positions / runs / memory       │
       │   - Mastra PostgresStore: threads /          │
       │     messages / workflow_snapshots / memory   │
       └──────────────────────────────────────────────┘

       ┌──────────────────────────────────────────────┐
       │   外部依赖                                    │
       │   - Binance API（CCXT）                       │
       │   - LLM provider（OpenAI / Anthropic 等）      │
       │   - Redis（job queue / pub-sub，Phase D 起）   │
       └──────────────────────────────────────────────┘
```

**核心服务职责**：

| 服务 | 端口（建议） | 核心职责 | 主依赖 |
|---|---|---|---|
| `apps/web` | 3000 | UI + 认证 + Mastra 挂载点 | Next.js 16 / CopilotKit / better-auth |
| `services/data` | 8001 | 行情接入 / 历史回放 / 实时订阅 | CCXT / akshare（后期） |
| `services/paper` | 8002 | 内核：Clock / MessageBus / Strategy / Gateway / Engine；回测 + 模拟盘（同代码可延伸实盘，不在当前计划） | Python kernel（自研） |
| `services/research` | 8003 | LLM 多 agent 决策（TradingAgents 风格，Mastra 重写） | OpenAI/Anthropic SDK |
| `services/factor` | 8004 | 因子库（pandas-ta / Alpha101 / qlib）+ IC 有效性检验，只产出信号（已落地 D-11） | qlib + 自研 |

---

## 内核关键抽象（Python，`services/paper/src/kernel/`）

> 接口设计参考 Nautilus，**砍掉 Rust 部分用纯 Python 实现**。等 MVP 跑通后再考虑迁移
> hot path。

### Clock

```python
# kernel/clock.py
from abc import ABC, abstractmethod
from dataclasses import dataclass

class Clock(ABC):
    """时间源抽象 —— 内核所有时间相关动作经此获取当前时间。

    回测时是 TestClock（数据驱动），实盘 / 模拟盘是 LiveClock（系统时钟）。
    """
    @abstractmethod
    def now_ns(self) -> int: ...
    @abstractmethod
    def now(self) -> datetime: ...
    @abstractmethod
    def set_timer(self, name: str, interval_ns: int, callback: Callable) -> None: ...

class TestClock(Clock):
    def __init__(self, initial_ns: int): ...
    def set_time(self, ns: int) -> None: ...
    def advance_time(self, to_ns: int) -> list[TimeEvent]: ...

class LiveClock(Clock):
    def __init__(self): ...   # 用 time.time_ns()
```

### MessageBus

```python
# kernel/msgbus.py
from typing import Callable, Any

class MessageBus:
    """pub/sub + endpoint 双形态。

    pub/sub：topic 通配（*/?），broadcast 多订阅者
    endpoint：点对点，注册一个 handler 接收命令
    """
    def publish(self, topic: str, msg: Any) -> None: ...
    def subscribe(self, topic_pattern: str, handler: Callable[[Any], None]) -> None: ...
    def register_endpoint(self, endpoint: str, handler: Callable[[Any], None]) -> None: ...
    def send(self, endpoint: str, msg: Any) -> None: ...
```

### Strategy / Actor

```python
# kernel/actor.py
class Actor:
    """所有策略 / 自定义组件的父类。"""
    def __init__(self, config: ActorConfig): ...

    # 生命周期（用户覆写）
    def on_start(self) -> None: pass
    def on_stop(self) -> None: pass
    def on_bar(self, bar: Bar) -> None: pass
    def on_quote_tick(self, tick: QuoteTick) -> None: pass

    # 订阅 / 注册（不直接调 client，框架内部路由）
    def subscribe_bars(self, bar_type: BarType) -> None: ...
    def subscribe_quote_ticks(self, instrument_id: InstrumentId) -> None: ...
    def register_indicator_for_bars(self, bar_type: BarType, indicator: Indicator) -> None: ...

# kernel/strategy.py
class Strategy(Actor):
    """用户策略基类。在 Actor 基础上加下单接口。"""
    # 订单
    def submit_order(self, order: Order) -> None: ...
    def cancel_order(self, order: Order) -> None: ...
    def modify_order(self, order: Order, qty: Quantity | None, price: Price | None) -> None: ...

    # 持仓
    def close_position(self, position: Position) -> None: ...

    # 订单 / 持仓事件回调
    def on_order_filled(self, event: OrderFilled) -> None: pass
    def on_position_opened(self, event: PositionOpened) -> None: pass
    def on_position_closed(self, event: PositionClosed) -> None: pass
```

### Gateway（参考 vnpy）

```python
# kernel/gateway.py
class Gateway(ABC):
    """交易所 / 经纪商接入抽象。每个交易所一个独立子类。"""
    default_name: str
    default_setting: dict
    exchanges: list[Exchange]

    # 必须实现
    @abstractmethod
    def connect(self, setting: dict) -> None: ...
    @abstractmethod
    def close(self) -> None: ...
    @abstractmethod
    def subscribe(self, req: SubscribeRequest) -> None: ...
    @abstractmethod
    def send_order(self, req: OrderRequest) -> str: ...   # 返回 client_order_id
    @abstractmethod
    def cancel_order(self, req: CancelRequest) -> None: ...
    @abstractmethod
    def query_account(self) -> None: ...

    # 基类已实现 —— 把数据推回内核
    def on_tick(self, tick: QuoteTick) -> None:
        self._msgbus.publish(f"data.quotes.{tick.venue}.{tick.symbol}", tick)
    def on_order_event(self, event: OrderEvent) -> None: ...
    def on_position_event(self, event: PositionEvent) -> None: ...
```

### Engine（Backtest / Live 共用）

```python
# kernel/engine.py
class Kernel:
    """内核容器 —— 持 Clock、MessageBus、各引擎、缓存。

    BACKTEST / LIVE 两种环境注入不同 Clock + DataClient + ExecutionClient。
    """
    def __init__(self, environment: Literal["BACKTEST", "LIVE", "SANDBOX"], config: KernelConfig):
        self.clock: Clock = LiveClock() if environment != "BACKTEST" else TestClock(...)
        self.msgbus: MessageBus = MessageBus()
        self.cache: Cache = Cache()
        self.data_engine: DataEngine = DataEngine(...)
        self.risk_engine: RiskEngine = RiskEngine(...)
        self.execution_engine: ExecutionEngine = ExecutionEngine(...)
        self.portfolio: Portfolio = Portfolio(...)
        self.trader: Trader = Trader(...)

    def add_strategy(self, strategy: Strategy) -> None: ...
    def add_gateway(self, gateway: Gateway) -> None: ...
    def start(self) -> None: ...
    def stop(self) -> None: ...
```

### Order / Position / Bar / Quote

```python
# model/data.py
@dataclass(frozen=True)   # 不可变
class QuoteTick:
    instrument_id: InstrumentId
    bid_price: Price
    ask_price: Price
    bid_size: Quantity
    ask_size: Quantity
    ts_event: int      # 事件发生时间（venue 给的，ns）
    ts_init: int       # 系统接到时间（ns）

@dataclass(frozen=True)
class Bar:
    bar_type: BarType
    open: Price
    high: Price
    low: Price
    close: Price
    volume: Quantity
    ts_event: int
    ts_init: int

# model/orders.py
@dataclass
class Order:
    client_order_id: ClientOrderId       # 系统生成
    venue_order_id: VenueOrderId | None  # 交易所分配，回报里才有
    instrument_id: InstrumentId
    side: OrderSide                       # BUY / SELL
    type: OrderType                       # MARKET / LIMIT / STOP_LIMIT
    quantity: Quantity
    price: Price | None
    status: OrderStatus
    # 7 状态机起步（参考 Nautilus 14 状态裁剪到必要的）
    # NEW → SUBMITTED → ACCEPTED → PARTIALLY_FILLED → FILLED | CANCELED | REJECTED
```

### 6 个内核关键不变量

1. **回测 = 实盘同代码路径**：Strategy 子类 0 行改动，Kernel 注入不同 Clock + Client
2. **数据中心化**：策略只通过 data-service 取数据，**不直连交易所**
3. **策略不直接下单**：`submit_order` 把 Order 推 MessageBus，Risk → Execution → Gateway
4. **风控前置且强制**：所有 Order 必经 RiskEngine endpoint，**不可选不可跳**
5. **事件不可变 + 双时间戳**：`ts_event` + `ts_init` 全程保留，复盘可重放
6. **client_order_id ↔ venue_order_id 双向索引**：Cache 维护，重复 fill 静默丢弃

---

## Mastra 编排关键抽象（TypeScript，`packages/orchestration/src/`）

### Agent 拓扑

```typescript
// agents/orchestrator.ts
export const orchestrator = new Agent({
  name: 'orchestrator',
  instructions: `你是 Inalpha 总调度...`,
  model: anthropic('claude-opus-4-7'),
  agents: { traderAgent, riskAgent, researchHubAgent, swarmCoordAgent },
  tools: { dataGetBars, paperListStrategies, /* ... */ },
})

// agents/research-hub.ts —— 嵌套 supervisor，内含 4 analyst + 2 researcher + risk debate
export const researchHubAgent = new Agent({
  name: 'research-hub',
  agents: {
    fundamentalAnalyst, sentimentAnalyst, newsAnalyst, technicalAnalyst,
    bullResearcher, bearResearcher,
    aggressiveDebator, conservativeDebator, neutralDebator,
  },
  tools: { /* 9 个 tool 参考 TradingAgents */ },
})

// agents/trader.ts
export const traderAgent = new Agent({
  name: 'trader',
  instructions: `你只关心订单生命周期 —— 不做投研，不算风险...`,
  tools: { paperSubmitOrder, paperCancelOrder, paperGetPositions },
})

// agents/risk.ts
export const riskAgent = new Agent({
  name: 'risk',
  instructions: `你的立场和 trader 对立 —— 默认拒绝，直到证据充分。
                 审批通过时调 trade.approve_plan 派发一次性 approval_token。`,
  tools: { riskCheckOrder, riskGetExposure, approveTradePlan },
})
// 详见 docs/04-current-state.md 决策链路 sequence diagram。
```

### Workflow

```typescript
// workflows/deep-research.ts
export const deepResearchWorkflow = createWorkflow({
  id: 'deep-research',
  inputSchema: z.object({ symbol: z.string(), asOf: z.string() }),
  outputSchema: ResearchPlanSchema,
})
  // 1. 4 个 analyst 并行（改进 TradingAgents 原版顺序）
  .parallel([fundamentalsStep, sentimentStep, newsStep, technicalStep])
  // 2. Bull vs Bear 辩论（dowhile + count）
  .map(({ inputData }) => ({ ...inputData, debate: { count: 0, history: '' } }))
  .dowhile(researcherStep, ({ inputData }) => inputData.debate.count < 2 * MAX_DEBATE_ROUNDS)
  // 3. Research Manager 裁决
  .then(researchManagerStep)
  // 4. Trader 提案
  .then(traderProposalStep)
  // 5. 三方 risk 辩论
  .dowhile(riskDebatorStep, ({ inputData }) => inputData.risk.count < 3 * MAX_RISK_ROUNDS)
  // 6. Portfolio Manager 最终
  .then(portfolioManagerStep)
  .commit()

// workflows/strategy-lifecycle.ts
export const strategyLifecycle = createWorkflow({ id: 'strategy-lifecycle' })
  .then(fetchHistoricalDataStep)       // data-service
  .then(runBacktestStep)                // paper-service backtest
  .then(evalBacktestStep)               // 算 sharpe / drawdown
  .branch([
    [(o) => o.sharpe > 1.0, startPaperTradingStep],  // 通过 → 上模拟盘
    [(_) => true, rejectStep],                        // 不通过 → 告知用户
  ])
  .commit()
```

### Tools 清单（当前实现）

> 本节原为 Phase C 的 MVP 设想，已更新为 **当前实际暴露的 tool 族**（2026-06-05）。
> 权威清单以代码为准：`packages/orchestration/src/tools/` + `agents/orchestrator.ts`。

| Tool 族 | 代表 tool | 服务 / 路径 | 用途 |
|---|---|---|---|
| **data.\*** | `data.get_bars`（默认 `fresh=True`）`.get_ticker` `.backfill_bars` `.get_fundamentals` | data:8001 | K 线 / 现价 / 补数 / 财报基本面 |
| **web.\*** | `web.search` `web.search_news` | data:8001 `/web/*` | 零密钥网络情报（DDGS 多引擎） |
| **factor.\*** | `factor.timing` `factor.score` `factor.catalog` | factor:8004 | 因子择时 / 打分 / 目录（IC 有效性） |
| **research.\*** | `research.deep_dive` | research:8003 | 多 analyst + bull/bear 辩论 → `StrategyHint` |
| **paper.\*（回测/策略）** | `paper.run_backtest` `.compose_strategy` `.author_strategy` `.list_candidates` `.get_candidate` `.promote_candidate` | paper:8002 | 回测（自动并跑 baseline）/ LLM 自创策略（沙盒）/ 候选 leaderboard / 审批门 |
| **paper.\*（模拟盘）** | `paper.start_strategy` `.stop_strategy` `.list_strategy_runs` `.list_strategy_run_decisions` `.list_orders` `.list_positions` | paper:8002 | live runner 起停 / 运行状态 / 决策复盘 / 持仓 |
| **trade.\***（下单护栏三件套） | `trade.create_plan` → `.approve_plan` → `.execute_plan`（+ `.reject_plan` `.get_plan`） | orchestration + paper `/orders/submit` | 两阶段批准，approval_token 一次性 + 5min TTL，`execute_plan` 是**唯一**有 side-effect 的下单 tool |
| **swarm.\*** | `swarm.run_backtest_grid` | paper:8002 | 参数网格批量回测（grid-size-cap 守门） |
| **mcp__\<server\>__\*** | `mcp__coingecko__*` … | 外部 MCP | 可插拔外部源，走同一套 hooks + permissions；默认只启零密钥端点 |

> **执行链路**：`trade.* → Hooks (PreToolUse) → Permission Engine → Plan Store → /orders/submit`。
> LLM 视野里**没有**直接 `submit_order` 路径——旧 `paper.submit_order_intent` /
> `live.submit_order` 全部 `deny` 或 `modelInvocable:false`。详见
> [`docs/04-current-state.md`](./04-current-state.md)。

---

## 模块依赖图

```
apps/web (Next.js)
   ↓ imports
packages/orchestration (Mastra)
   ↓ HTTP+WS
services/* (FastAPI)
   ↓ uses
shared-py (internal Python lib: kernel / model / utils)

服务之间：
data-service ◄─── paper-service（用于历史回放和实盘数据订阅）
data-service ◄─── research-service（取行情给 analyst）
data-service ◄─── factor-service（取行情给因子计算）
research-service ──► paper-service.submit_order_intent（决策落地）
```

**禁止**的依赖（防止循环）：

- paper-service **不** import research-service（避免内核依赖 LLM）
- factor-service **不** import paper-service（因子只产出信号）
- data-service **不** import 任何其他服务（最底层）

---

## MVP 端到端流程

```
用户："帮我研究 BTC 这周，建议好的话上模拟盘"
   │
   ▼
[apps/web] CopilotKit 发消息到 Mastra
   │
   ▼
[Mastra orchestrator] LLM 解析意图，发现两个动作：
   ├─► tool: research.deep_dive({ symbol: "BTC/USDT", asOf: "2026-05-21" })
   │      ▼
   │   [research-service] POST /deep_dive
   │      ├─ 调 data-service GET /bars / 抓 Reddit / News
   │      ├─ 4 analyst 并行调 LLM
   │      ├─ bull vs bear 1 轮辩论
   │      ├─ research manager 输出 plan { rating: "Overweight", thesis: "..." }
   │      └─ trader agent 输出 { action: "BUY", price: 65000, stop: 63500, size: 0.05 BTC }
   │      ▼
   │   返回结构化决策给 Mastra
   │
   ├─► [orchestrator] LLM 决定：rating Overweight → 上模拟盘
   │      ▼
   │   tool: paper.start_strategy({
   │     strategy: "ResearchDrivenSMA",
   │     params: { symbol: "BTC/USDT", target_position: 0.05, stop_loss: 63500 }
   │   })
   │      ▼
   │   [paper-service]
   │      ├─ 启动 Strategy 实例（LiveClock + Binance Gateway + 虚拟撮合）
   │      ├─ 内核 Clock loop 开始跑
   │      └─ 返回 strategyId 给 Mastra
   │      ▼
   │   订阅 WS /strategy/{id}/events 监听后续成交
   │
   ▼
[Mastra orchestrator] 综合两个 tool 结果，回复用户：
   "已完成研究：Overweight 评级，理由 X / Y / Z。
    已在模拟盘启动策略 #123，目标仓位 0.05 BTC，止损 63500。
    我会持续监控，触发条件时告诉你。"
```

---

## 目录结构（Phase D 起 mkdir）

```
inalpha/
├── apps/
│   └── web/                    # Next.js 16 + CopilotKit
│       ├── app/                # App Router
│       ├── components/
│       └── package.json
├── packages/
│   ├── orchestration/          # Mastra
│   │   ├── src/
│   │   │   ├── agents/
│   │   │   ├── workflows/
│   │   │   ├── tools/
│   │   │   ├── memory/
│   │   │   └── index.ts
│   │   └── package.json
│   └── shared-types/           # TS types 给 apps/web 用
├── services/
│   ├── data/
│   │   ├── src/
│   │   │   ├── api/            # FastAPI 路由
│   │   │   ├── connectors/     # CCXT / akshare 接入
│   │   │   ├── storage/        # TimescaleDB 读写
│   │   │   └── main.py
│   │   └── pyproject.toml
│   ├── paper/
│   │   ├── src/
│   │   │   ├── api/
│   │   │   ├── kernel/         # ⭐ Clock/MessageBus/Strategy/Gateway/Engine
│   │   │   ├── model/          # ⭐ Order/Position/Bar/Quote dataclass
│   │   │   ├── strategies/     # 用户策略实现
│   │   │   ├── gateways/       # binance/ ...
│   │   │   └── main.py
│   │   └── pyproject.toml
│   ├── research/
│   │   ├── src/
│   │   │   ├── api/
│   │   │   ├── agents/         # 12 个 agent prompt（TradingAgents 移植）
│   │   │   ├── tools/          # LLM 可调的数据工具
│   │   │   ├── memory/         # TradingMemoryLog 移植（用 Postgres 替文件）
│   │   │   └── main.py
│   │   └── pyproject.toml
│   └── factor/                 # Phase F+
├── infra/
│   ├── docker-compose.yml      # postgres+timescale / redis / 各 service
│   └── migrations/             # Alembic
├── docs/                       # 本目录（计划 + 设计文档）
├── _refs/                      # 4 个参考 repo 的 sparse-clone（gitignored）
├── README.md
├── .gitignore
└── package.json                # workspace root
```

---

## Phase D 启动清单（下一轮工作）

按这个顺序起 packages：

1. **infra**：docker-compose 起 postgres + timescaledb + redis；写 0000 migration（建 Inalpha 自己的表）
2. **services/data**（最底层）：FastAPI 骨架 + CCXT Binance 连接 + 1 个 endpoint `GET /bars/{symbol}` + 1 个 WS `/ticks/{symbol}`
3. **services/paper**：先内核（Clock / MessageBus / Order / Bar）+ 1 个 strategy（SMA cross）+ backtest endpoint
4. **packages/orchestration**：先 1 个 agent（orchestrator）+ 2 个 tool（`data.get_bars` / `paper.run_backtest`）+ 1 个 workflow（`backtest-and-report`）
5. **apps/web**：Next.js 16 起项目 + CopilotKit + AG-UI 接到 orchestration
6. **services/research**：FastAPI 骨架 + 1 个 analyst（fundamental，最简）+ deep_dive endpoint
7. 串通 1 → 6：用户能说"帮我用 SMA 跑 BTC 最近 30 天"得到回测报告

预计 1-2 周完成 MVP 骨架（不含调优）。

---

## 验证标准（MVP 完成的判定）

一句话：**用户在浏览器对话框里说一句话，能拿到 LLM 研究 + 回测 / 模拟盘结果，全程不写代码**。

具体 demo 流程（手工 QA）：

1. 启动：`docker compose up -d` + `pnpm dev`
2. 浏览器打开 localhost:3000，登录
3. 对话框输入："研究 BTC 这周，建议用 SMA cross 试一下，跑回测看看"
4. 期望：30-90 秒内返回，包含
   - 4 个 analyst 简报
   - Research Manager 评级
   - SMA cross 在 BTC 最近 30 天的回测报告（sharpe / drawdown / 总收益）
   - 后续建议（是否上模拟盘）
5. 输入："好的，上模拟盘 0.01 BTC，止损 63000"
6. 期望：在 5 秒内启动模拟盘策略，返回 strategyId，开始监听成交

测试覆盖（自动）：

- `services/paper` 内核单测覆盖 ≥70%（Clock / MessageBus / 状态机）
- 一份 e2e 测试：跑通 backtest → start paper → wait fill → assert position

