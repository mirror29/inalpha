# 03 · inalpha 内核架构（正式版）

> 状态：**Phase C 正式设计**，基于 Phase B 4 份 repo 拆解结论 + 用户锁定决策。
> 取代 `01-architecture-overview.md` 中的 high-level 草图；01 保留作快照。

## 锁定决策（Phase C·2026-05-21）

| 维度 | 决策 | ADR |
|---|---|---|
| 内核语言 | **MVP 纯 Python**，后期 Rust 优化 hot path | [0004](decisions/0004-kernel-language.md) |
| MVP 范围 | **Crypto（Binance 起步）+ TradingAgents 风格多 agent 研究** | 本文 §MVP |
| 时序数据库 | **Postgres + TimescaleDB**（与 Mastra PostgresStore 同台） | [0003](decisions/0003-timeseries-db.md) |
| 跨服务通信 | **HTTP REST + WebSocket** 组合 | [0002](decisions/0002-cross-service-communication.md) |
| 编排框架 | Mastra（TypeScript） | [0001](decisions/0001-mastra-orchestration.md) |
| 多 agent | Mastra supervisor pattern，**不嵌 LangGraph** | [0001](decisions/0001-mastra-orchestration.md) |
| Swarm worker 池 | 在各 engine 服务内部（RQ → Celery） | [0005](decisions/0005-swarm-worker-pool.md) |

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
| `services/paper` | 8002 | 内核：Clock / MessageBus / Strategy / Gateway / Engine；回测 + 模拟盘 + 实盘 | Python kernel（自研） |
| `services/research` | 8003 | LLM 多 agent 决策（TradingAgents 风格，Mastra 重写） | OpenAI/Anthropic SDK |
| `services/factor` | 8004 | ML 因子 pipeline（Phase F+，先占位） | qlib |

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
  instructions: `你是 inalpha 总调度...`,
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
  instructions: `你的立场和 trader 对立 —— 默认拒绝，直到证据充分...`,
  tools: { riskCheckOrder, riskGetExposure },
})
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

### Tools 清单（MVP 范围）

| Tool ID | 调用 | 用途 |
|---|---|---|
| `data.get_bars` | `data-service` `GET /bars` | 取 K 线 |
| `data.subscribe_ticks` | `data-service` `WS /ticks` | 订阅实时 tick（仅 workflow 内部用） |
| `paper.run_backtest` | `paper-service` `POST /backtest` | 跑回测，返回 jobId |
| `paper.start_strategy` | `paper-service` `POST /strategy/start` | 上模拟盘 |
| `paper.stop_strategy` | `paper-service` `POST /strategy/stop` | 停模拟盘 |
| `paper.get_positions` | `paper-service` `GET /positions` | 查持仓 |
| `paper.submit_order_intent` | `paper-service` `POST /orders/intent` | 提交下单意图（经 risk） |
| `risk.check_order` | `paper-service` `POST /risk/check` | 风控预检 |
| `research.deep_dive` | `research-service` `POST /deep_dive` | 单次跑 TradingAgents 风格流程 |
| `factor.compute_alpha` | `factor-service` `POST /alpha` | 算因子（Phase F+） |

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
├── docs/                       # 本目录（计划 + 拆解 + ADR）
├── _refs/                      # 4 个参考 repo 的 sparse-clone（gitignored）
├── README.md
├── .gitignore
└── package.json                # workspace root
```

---

## Phase D 启动清单（下一轮工作）

按这个顺序起 packages：

1. **infra**：docker-compose 起 postgres + timescaledb + redis；写 0000 migration（建 inalpha 自己的表）
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

---

## 后续 ADR 待写

- `0002` HTTP REST + WebSocket 通信协议（本 Phase）
- `0003` Postgres + TimescaleDB 选型（本 Phase）
- `0004` 内核语言：MVP Python，Rust 迁移路径（本 Phase）
- `0006` 风控规则 spec（Phase D 起 packages 时写）
- `0007` Memory schema（research-service 把 TradingMemoryLog 移植到 Postgres 时）
- `0008` Agent prompt 版本管理（避免 prompt 改了无法 reproduce）
