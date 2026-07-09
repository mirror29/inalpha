# 01 · 架构总览

> 状态：**现行架构总览**（2026-06-05）。
> 本文给"整体形态 + 各层职责 + 关键不变量"的高层视图；内核事件循环 / Clock /
> MessageBus / 撮合 / 风控的详细设计见 [`03-kernel-design.md`](./03-kernel-design.md)；
> 逐里程碑的落地状态见 [`04-current-state.md`](./04-current-state.md)。

## 三层形态

```
┌──────────────────────────────────────────────────────────────────────┐
│  入口层（当前）                                                         │
│  mastra :4111    对话 + 实时 trace（主入口：tool call / hook / token）  │
│  apps/dashboard  运营控制台（app.inalpha.dev · :3001 · 只读看板 + BFF） │
│  apps/web        静态官网（inalpha.dev）；CopilotKit 对话 UI 规划 Phase E+│
└───────────────────────────────┬──────────────────────────────────────┘
        dashboard: 同源 /api/* → BFF（dev token 转发，token 不进浏览器）
┌───────────────────────────────▼──────────────────────────────────────┐
│  编排层 · packages/orchestration · Mastra (TypeScript)                 │
│                                                                        │
│   agents/      orchestrator → trader / risk（按市场分类自动路由 venue）│
│   tools/       data.* web.* factor.* research.* paper.* trade.* swarm.*│
│                + mcp__<server>__<verb>（可插拔外部 MCP）               │
│   hooks/       5 类生命周期事件 + Stop（PreToolUse / PostToolUse / …） │
│   permissions/ allow / ask / deny 三态（deny > allow > ask > default） │
│   plan/exec    create_plan → approve_plan → execute_plan（一次性 token）│
│   memory/      PostgresStore · 用户偏好 / 历史会话 / plan 状态          │
└───────────────────────────────┬──────────────────────────────────────┘
                                │ HTTP / MCP（每个 tool 调对应服务）
        ┌───────────────┬───────┴───────┬───────────────┐
        ▼               ▼               ▼               ▼
  ┌──────────┐   ┌──────────────┐ ┌──────────────┐ ┌──────────────┐
  │  data    │   │  paper       │ │  research    │ │  factor      │
  │  :8001   │   │  :8002       │ │  :8003       │ │  :8004       │
  │ 行情/财报 │   │ 内核+回测+   │ │ 多 analyst   │ │ 因子库 + IC  │
  │ /web/fx  │   │ 模拟盘+沙盒  │ │ + bull/bear  │ │ 有效性       │
  └─────┬────┘   └──────┬───────┘ └──────────────┘ └──────────────┘
        │               │   （services/_shared：跨服务基础设施，改前评估）
        ▼               ▼
  ┌──────────────────────────────────┐
  │  Postgres 17 + TimescaleDB        │  hypertable: bars / ticks / orders
  │                                   │  常规表: accounts / positions / runs / plans
  └──────────────────────────────────┘
        ▲
        │  外部数据源 / 经纪商
        └── CCXT(crypto) · akshare(A股/港股) · yfinance(美股/全球) · FRED · DDGS(web)
```

> 启动后端 + 编排：`pnpm i && uv sync && bash scripts/dev.sh up`
> （data:8001 + paper:8002 + research:8003 + factor:8004 + mastra:4111，各带 `/health`）。
> 运营控制台另起：`cd apps/dashboard && pnpm dev`（:3001，BFF 连后端）。

## 各层职责

### 入口层

当前用户入口两个，面向不同用途：

- **`mastra dev` playground（:4111）** — 跟 orchestrator agent 对话，并在 live trace UI 里
  看每个 tool call / hook 事件 / approval token。当前主要的"对话 + 操作"入口。
- **`apps/dashboard`**（`:3001` · `app.inalpha.dev`）— **只读运营控制台**：把"原本要问
  agent 才知道的运行时状态"（账户 / 持仓 / live runner / agent 活动 / 回测史）变成一眼可见
  的盘面，让对话回归"决策 / 操作"本职。动态 Next（Node 运行时）用 Route Handler 当 **BFF**——
  浏览器只调同源 `/api/*`，server 侧用 dev token 转发到后端（Python service 未配 CORS + 需
  JWT，token 不进浏览器）。单用户 dev token、非多租户产品；当前已落地**组合总览 MVP**。

`apps/web`（`inalpha.dev`）当前是静态官网（`output:"export"` → Cloudflare Pages，品牌 /
文档）；面向终端用户的 CopilotKit 对话 UI 规划在 Phase E+，尚未接后端。

### 编排层 · `packages/orchestration`（Mastra / TypeScript）

Inalpha 的"大脑 + 护栏"。三件事：

1. **把每个核心服务的能力封装成 tool**（`<service>.<verb>` 命名），按市场分类自动路由 venue。
2. **把 LLM 关在交易路径外**——四层防御（详 `03` / 博客篇 1）：
   - **tool 集分桶**：orchestrator 看不到直下单 tool
   - **permissions deny-list**：`live.*` / 直下单恒 deny，不可被 hook 覆盖
   - **plan/exec 两阶段**：`create_plan → approve_plan → execute_plan`，approval_token
     一次性 + 5min TTL，LLM 永不持有 token
   - **审计签名**：PostToolUse hook 强制写脱敏 + 签名的审计日志
3. **可插拔 MCP**：`mcp__<server>__<verb>` 走同一套 hooks + permissions；默认只启用零密钥
   公开端点，付费连接器以 `disabled:true` 作模板。

### 服务层 · `services/*`（Python · FastAPI）

| 服务 | 端口 | 职责 |
|---|---|---|
| **data** | 8001 | 行情接入 + 时序存储 + 历史回放；`/bars`（默认 `fresh=True`）`/ticker` `/fundamentals` `/web/search` `/fx`。CCXT + akshare + yfinance + FRED + DDGS |
| **paper** | 8002 | 事件驱动内核（Clock / MessageBus / 撮合 / 风控）+ 回测引擎 + **live runner**（模拟盘按行情自动跑）+ **strategy_authoring**（LLM 自创策略三道沙盒 + fitness） |
| **research** | 8003 | LLM 多 analyst（fundamental / sentiment / technical / valuation …）+ bull/bear 辩论 → `StrategyHint`，不直接下单 |
| **factor** | 8004 | 因子库（pandas-ta / Alpha101 / qlib）+ IC 有效性检验；`factor.timing / .score / .catalog`，只产出信号 |
| **_shared** | — | 跨服务基础设施（DataClient / 错误类型 / auth …），改前评估 |

**核心不变量：回测 = 模拟盘 同代码（架构上可延伸到实盘，但真钱实盘不在当前计划）。** 同一份 `Strategy` 文件，只换 Clock
（`TestClock` / `LiveClock`）+ Gateway（模拟撮合 / 真实经纪商）；行为差异源于物理
（slippage / latency），不源于两套代码路径。这是审计链能成立的物理前提——只有一个
文件，签名才有得指。

## 跨服务依赖约束（架构决策，禁止违反）

```
paper  ✗ import research   （内核不依赖 LLM）
factor ✗ import paper      （因子只产出信号）
data   ✗ import 任何其他服务 （最底层）
```

协作只走 HTTP / MCP：research → paper 传 `StrategyHint`；paper ← data 拉 bars/fx/
fundamentals；paper → risk 同进程前置守门（所有 Order 撮合前过 RiskGuard）。

## 关键不变量（写代码前定下、别动）

1. **回测 = 模拟盘 同代码**：同一份 Strategy 代码，仅 Clock + Gateway 切换（架构上可延伸到实盘，真钱实盘不在当前计划）
2. **数据中心化**：所有服务只从 data-service 取数据，不私自爬交易所
3. **策略不直接下单**：策略产出 `Order`，由 Execution Engine + 风控决定怎么发
4. **风控前置**：所有 Order 进 Execution 前先过 RiskGuard（HTTP 路径强制）
5. **LLM 无直下单路径**：tool 分桶 + permissions deny + plan/exec token + 审计签名
6. **金融时效性**：读行情/新闻默认 `fresh=True`；freshness 看 `bars[-1].ts` 距 as_of
   的间隔，不看 bar 数量；数据不可用时显式降级 + 标低 confidence，不静默用过时数据

## 延伸阅读

- 内核事件循环 / Clock / 撮合 / 风控详设 → [`03-kernel-design.md`](./03-kernel-design.md)
- 逐里程碑落地状态 + 一次下单端到端时序 → [`04-current-state.md`](./04-current-state.md)
- 项目背景 / 边界 / 完成度快照 → [`00-context.md`](./00-context.md)
- AI 协作硬约束 → [`../AGENTS.md`](../AGENTS.md) · [`../CLAUDE.md`](../CLAUDE.md)
