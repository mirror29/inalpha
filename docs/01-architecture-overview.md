# 01 · 架构总览

> 状态：**现行架构总览**（2026-09-30 代码核对）。
> 本文给"整体形态 + 各层职责 + 关键不变量"的高层视图；内核事件循环 / Clock /
> MessageBus / 撮合 / 风控的详细设计见 [`03-kernel-design.md`](./03-kernel-design.md)；
> 逐里程碑的落地状态见 [`04-current-state.md`](./04-current-state.md)。

## 三层形态

```
┌──────────────────────────────────────────────────────────────────────┐
│  入口层（当前）                                                         │
│  mastra :4111    Agent 编排 API + trace                                   │
│  apps/dashboard  认证控制台（app.inalpha.dev · :3001 · 对话 + BFF + 看板）│
│  apps/web        静态官网（inalpha.dev）                                  │
└───────────────────────────────┬──────────────────────────────────────┘
        dashboard: 同源 /api/* → BFF（逐用户 JWT；模型密钥服务端加密保存）
┌───────────────────────────────▼──────────────────────────────────────┐
│  编排层 · packages/orchestration · Mastra (TypeScript)                 │
│                                                                        │
│   agents/      orchestrator → trader / risk（按市场分类自动路由 venue）│
│   tools/       data.* web.* factor.* research.* paper.* trade.* evolver.*│
│                swarm.*                                                  │
│                + mcp__<server>__<verb>（可插拔外部 MCP）               │
│   hooks/       5 类生命周期事件 + Stop（PreToolUse / PostToolUse / …） │
│   permissions/ allow / ask / deny 三态（deny > allow > ask > default） │
│   plan/exec    create_plan → approve_plan → execute_plan（一次性 token）│
│   memory/      PostgresStore · 用户偏好 / 历史会话；plan 由 paper DB 持久化│
└───────────────────────────────┬──────────────────────────────────────┘
                                │ HTTP / MCP（每个 tool 调对应服务）
        ┌──────────┬──────────┬──────────┬──────────┬──────────┐
        ▼          ▼          ▼          ▼          ▼
  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐
  │ data     │ │ paper    │ │ research │ │ factor   │ │ evolver  │
  │ :8001    │ │ :8002    │ │ :8003    │ │ :8004    │ │ :8005    │
  │行情/财报 │ │内核/回测 │ │多分析师  │ │因子/IC   │ │策略演化  │
  │/web/fx   │ │/模拟盘   │ │与辩论    │ │与衰减    │ │与审计    │
  └─────┬────┘ └─────┬────┘ └──────────┘ └──────────┘ └─────┬────┘
        │               │   （services/_shared：跨服务基础设施，改前评估）
        ▼               ▼
  ┌──────────────────────────────────┐
  │  Postgres 17 + TimescaleDB        │  hypertable: bars / ticks
  │                                   │  常规表: orders / accounts / positions / runs / plans
  └──────────────────────────────────┘
        ▲
        │  外部数据源 / 经纪商
        └── CCXT(crypto) · akshare(A股/港股) · yfinance(美股/全球) · FRED · DDGS(web)
```

> 安装依赖后，先按根 README 复制根 `.env` 与 `infra/.env`、启动开发 Compose 并执行 Alembic
> migration；随后从仓库根运行 `bash scripts/dev.sh up`。
> （data:8001 + paper:8002 + research:8003 + factor:8004 + evolver:8005 + mastra:4111，各 service 带 `/health`）。
> 运营控制台另起：`cd apps/dashboard && pnpm dev`（:3001，BFF 连后端）。

## 各层职责

### 入口层

当前主要入口两个，面向不同用途：

- **`mastra dev` playground（:4111）** — 跟 orchestrator agent 对话，并在 live trace UI 里
  看每个 tool call / hook 事件 / approval token。开发调试入口。
- **`apps/dashboard`**（`:3001` · `app.inalpha.dev`）— **认证后的操作者控制台**：提供
  agent 对话、组合 / 持仓 / live runner / 演化 / 回测 / 因子 / 风控等页面。动态 Next
  Route Handler 作为 **BFF**，浏览器只访问同源 `/api/*`；服务端按登录用户签发 JWT，
  用户级 LLM 配置加密保存，明文密钥不写入演化记录或浏览器持久状态。

`apps/web`（`inalpha.dev`）是静态官网（`output:"export"` → Cloudflare Pages，品牌 / 文档）。

### 编排层 · `packages/orchestration`（Mastra / TypeScript）

Inalpha 的"大脑 + 护栏"。三件事：

1. **把每个核心服务的能力封装成 tool**（`<service>.<verb>` 命名），按市场分类自动路由 venue。
2. **把 LLM 关在交易路径外**——四层防御（详 `03` / 博客篇 1）：
   - **tool 集分桶**：orchestrator 看不到直下单 tool
   - **permissions deny-list**：`live.*` / 直下单恒 deny，不可被 hook 覆盖
   - **plan/exec 两阶段**：`create_plan → approve_plan → execute_plan`；模型只能把审批 tool
     返回的一次性、5min TTL `approval_token` 传给 execute，不能访问或绕过底层下单 tool
   - **审计分层**：交易计划、审批、演化 run/candidate 等领域记录持久化到 Postgres；hook
     telemetry 脱敏后 best-effort 写出，不替代领域审计事实
3. **可插拔 MCP**：`mcp__<server>__<verb>` 走同一套 hooks + permissions；默认只启用零密钥
   公开端点，付费连接器以 `disabled:true` 作模板。

### 服务层 · `services/*`（Python · FastAPI）

| 服务 | 端口 | 职责 |
|---|---|---|
| **data** | 8001 | 行情接入 + 时序存储 + 历史回放；`/bars`（默认 `fresh=True`）`/ticker` `/fundamentals` `/web/search` `/fx`。CCXT + akshare + yfinance + FRED + DDGS |
| **paper** | 8002 | 事件驱动内核（Clock / MessageBus / 撮合 / 风控）+ 回测引擎 + **live runner**（模拟盘按行情自动跑）+ **strategy_authoring**（LLM 自创策略三道沙盒 + fitness） |
| **research** | 8003 | LLM 多 analyst（fundamental / sentiment / technical / valuation …）+ bull/bear 辩论 → `StrategyHint`，不直接下单 |
| **factor** | 8004 | 因子库（pandas-ta / Alpha101 / qlib）+ IC 有效性检验；`factor.timing / .score / .catalog`，只产出信号 |
| **evolver** | 8005 | E1 单代 unified-diff 变异与候选审计；E2 事件共演化与持久化 EvolutionLoop（feature flag），拥有预算、调度、租约、checkpoint、冠军锁定和 holdout；不会自动 promote / 启动策略 / 下单 |
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
evolver ✗ 绕过 data / owner auth（bars 由 data 获取；用户密钥只可按 owner 即时读取）
```

协作只走 HTTP / MCP：research → paper 传 `StrategyHint`；paper ← data 拉 bars/fx/
fundamentals；paper → risk 同进程前置守门（所有 Order 撮合前过 RiskGuard）。

## 关键不变量（写代码前定下、别动）

1. **回测 = 模拟盘 同代码**：同一份 Strategy 代码，仅 Clock + Gateway 切换（架构上可延伸到实盘，真钱实盘不在当前计划）
2. **数据中心化**：所有服务只从 data-service 取数据，不私自爬交易所
3. **策略不直接下单**：策略产出 `Order`，由 Execution Engine + 风控决定怎么发
4. **风控前置**：所有 Order 进 Execution 前先过 RiskGuard（HTTP 路径强制）
5. **LLM 无直下单路径**：tool 分桶 + permissions deny + plan/exec token + 领域审计
6. **金融时效性**：读行情/新闻默认 `fresh=True`；freshness 看 `bars[-1].ts` 距 as_of
   的间隔，不看 bar 数量；数据不可用时显式降级 + 标低 confidence，不静默用过时数据
7. **演化授权且可复现**：产生 LLM 费用前冻结非密钥配置、定价与数据 manifest。E1 每次新计费 run
   显式审批；E2 点击或明确指令授权受预算约束的研究 Loop，内部迭代不逐代审批。启动 grant
   绑定 owner / operation / config digest，Loop 启动有效期为 5 分钟；后续执行依赖持久化授权
   （90 天有效）与有效租约，逐次模型请求检查预算和 fencing。密钥兑换 credential grant
   最长 30 小时，不作为整个 Loop 的执行授权。候选永不自动 promote、启动或下单

## E2 持久化研究闭环

`EvolutionLoop` 按 owner 与 target 复用活动任务；数据库轮询调度不依赖对话存活。
Loop/campaign 通过过期租约与 fencing token 恢复，proposal checkpoint 复用已提交的提案，
阶段交接及 UI 事件原子提交。Paper 提供独立 Forward 沙盒，持久化闭合 bars、事实与撮合，
以签名证据返回 Evolver；这些记录不进入普通账户、持仓、订单或 Runner。

唯一冠军需通过至少 30 天、三个独立事件的 Forward（最多 90 天），随后执行一次封存
holdout；崩溃恢复沿用同一 attempt。通过后仍需手动采用为 `runner_eligible=false` 的
实验性研究资产。代码与自动化测试已落地，真实运行、重启和费用证据尚待验证；下一步统一见
[`04-current-state.md`](./04-current-state.md)；闭环决策见
[`designs/e2-event-evolution-loop.md`](./designs/e2-event-evolution-loop.md)。

## 延伸阅读

- 内核事件循环 / Clock / 撮合 / 风控详设 → [`03-kernel-design.md`](./03-kernel-design.md)
- 逐里程碑落地状态 + 一次下单端到端时序 → [`04-current-state.md`](./04-current-state.md)
- 项目背景 / 边界 / 完成度快照 → [`00-context.md`](./00-context.md)
- AI 协作硬约束 → [`../AGENTS.md`](../AGENTS.md) · [`../CLAUDE.md`](../CLAUDE.md)
