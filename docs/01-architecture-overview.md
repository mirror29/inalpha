# 01 · 自建系统顶层架构草图

> 状态：**草图**。Phase B 完成 4 份 repo 拆解后会回头修订。本文件给整体形态画饼，
> 不是最终设计。

## 总体形态

```
  ┌────────────────────────────────────────────────────────────────────────┐
│                       用户入口（浏览器 + 对话）                           │
│                   Next.js (App Router) + CopilotKit                    │
└───────────────────────────────┬────────────────────────────────────────┘
                                │ httpOnly cookie + JWT
                                ▼
┌────────────────────────────────────────────────────────────────────────┐
│                       Mastra 编排层（TypeScript / Node）                 │
│                                                                          │
│   ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌────────────┐ │
│   │  agents/     │  │  workflows/  │  │  tools/      │  │  memory/   │ │
│   │  - trader    │  │  - backtest  │  │  - data_*    │  │  - 用户偏好 │ │
│   │  - researcher│  │    → eval    │  │  - bt_*      │  │  - 历史会话 │ │
│   │  - risk      │  │    → paper   │  │  - live_*    │  │  - 仓位状态 │ │
│   │  - manager   │  │  - alpha     │  │  - factor_*  │  └────────────┘ │
│   │              │  │    research  │  │  - research_*│                  │
│   └──────────────┘  └──────────────┘  └──────────────┘                  │
└───────────────────────────────┬────────────────────────────────────────┘
                                │ HTTP / MCP（每个 tool 调对应服务）
        ┌───────────────────────┼─────────────────┬─────────────────┐
        ▼                       ▼                 ▼                 ▼
  ┌──────────┐           ┌──────────────┐  ┌──────────────┐  ┌──────────────┐
  │  data    │           │  backtest    │  │   live       │  │  factor      │
  │  service │◄─时序数据─ │   engine     │  │   engine     │  │   lab        │
  │ (Python) │           │ (Python/Rust)│  │ (Python/Rust)│  │  (Python)    │
  └─────┬────┘           └──────────────┘  └──────┬───────┘  └──────────────┘
        │                                          │
        ▼                                          ▼
  ┌──────────────┐                          ┌──────────────────┐
  │ TimeSeries   │                          │  外部市场 / 经纪商 │
  │ DB           │                          │  CCXT · IB · CTP  │
  │ (QuestDB /   │                          │  · OKX · Binance  │
  │ ClickHouse)  │                          └──────────────────┘
  └──────────────┘

  ┌──────────────────┐
  │  research        │
  │  service (LLM)   │  ◄── 多 agent 辩论决策（借鉴 TradingAgents）
  │  调外部 backend  │
  └──────────────────┘
```

## 5 个核心服务的职责

### 1. data-service

数据接入 + 时序存储 + 历史回放 + 实时订阅。

- 接入层：CCXT（crypto）+ akshare/tushare（A股）+ Alpaca/Polygon（美股）+
  OANDA（FX）+ vnpy gateways（CTP/XTP）
- 存储：QuestDB（轻量）或 ClickHouse（重型）
- 接口：HTTP REST（历史） + WebSocket（实时）
- **抽象决策依据**：Nautilus 的 `DataEngine` + vnpy 的 `gateway.subscribe()`

### 2. backtest-engine

- 事件驱动 + 时间源抽象（TestClock）
- 策略代码与 live-engine 完全一致（这是核心不变量）
- 撮合模拟器：从 L1 起步，逐步升级到 L2 order book replay
- **设计依据**：Nautilus 的 backtest 模块

### 3. live-engine

- 同样的事件循环，时间源替换为 LiveClock
- 通过 gateway 抽象接外部经纪商
- 模拟盘 = live-engine + 虚拟撮合（不下单到真实交易所）
- **设计依据**：Nautilus + vnpy gateway

### 4. factor-lab

- ML 因子 pipeline：Raw Data → Handler → Alpha → Model → Strategy
- 跟 backtest-engine 解耦：因子计算产出信号，由策略消费
- 实验管理：MLflow（qlib 也用这个）
- **设计依据**：qlib pipeline

### 5. research-service

- LLM 多 agent 研究：fundamental / sentiment / technical / risk / manager
- 输出研究报告 + 交易信号（不直接下单，交给策略消费）
- LLM 推理走外部 provider（OpenAI / Anthropic / DeepSeek / 自建），本服务不内嵌模型
- **设计依据**：TradingAgents 的角色分工

## Mastra 编排层的关键

Mastra 在 inalpha 里做的事：

1. **把每个核心服务的能力包装成 tool**，让 agent 能调用

   - `data.get_bars(symbol, start, end, timeframe)`
   - `backtest.run(strategy_id, params, period)`
   - `live.paper_start(strategy_id, params)`
   - `factor.compute_alpha(name, universe, date)`
   - `research.deep_dive(symbol)`
2. **把多步流程编排成 workflow**：

   - 用户说"帮我用 SMA 策略回测 BTC 最近一年并跑模拟盘"
   - → workflow: `data.fetch → backtest.run → eval.report → paper.start`
3. **把 LLM 研究 agent 嵌进对话**：

   - TradingAgents 的 4 个 analyst + Researcher Team + Risk Mgmt → Mastra agents
   - 通过 mastra/agent 编排辩论流程

## 关键不变量（写代码前定下来，别动）

1. **回测 = 实盘**：同一份 Strategy 代码，仅时间源和 gateway 切换
2. **数据中心化**：所有服务只从 data-service 取数据，不私自爬交易所
3. **策略不直接下单**：策略产出 `Order` 对象，由 Execution Engine 决定怎么发
4. **风控前置**：所有 Order 进 Execution 之前先过 Risk Manager
5. **Mastra tool 是唯一对外入口**：核心服务不暴露给前端，只暴露给 Mastra 编排层

## 用户入口

**独立 Next.js + CopilotKit**（apps/web）：

- Mastra 编排层挂在同一个 Next.js 进程下的 API Route（或独立 Node 服务）
- CopilotKit 通过 AG-UI 协议直连 Mastra，承担对话 UI + 流式响应 + 用户中途打断
- 认证 / 用户管理 / 历史会话归 Next.js（better-auth 或 NextAuth），httpOnly cookie + JWT
- 不依赖任何外部平台

## 后续工作钩子

- 等 4 份 ref 拆解完，**回头修订本文**，特别是：
  - 撮合模拟器具体设计（参考 Nautilus）
  - Gateway 接口最终签名（参考 vnpy）
  - 因子 / 模型 / 策略接口（参考 qlib）
  - 多 agent 编排细节（参考 TradingAgents）
