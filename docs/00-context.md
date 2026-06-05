# 00 · 项目背景与决策

> 本文回答"**Inalpha 是什么、为什么这么设计、现在做到哪**"。
> 架构总览见 [`01-architecture-overview.md`](./01-architecture-overview.md)；内核详设见
> [`03-kernel-design.md`](./03-kernel-design.md)；逐里程碑的落地状态见
> [`04-current-state.md`](./04-current-state.md)。

## 项目目标

自建一套 **AI agent 编排驱动的全球市场量化实验框架**，关键约束：

- **市场覆盖**：加密货币（CEX）/ 美股 / A股 / 港股 / 日韩澳印巴英德等单股 / 全球指数 /
  FRED 宏观——venue 路由由 orchestrator 按"市场分类"自动选
- **完整链路**：**回测 = 模拟盘共用同一份策略代码**（只换 Clock + Gateway；内核同代码设计上可延伸到实盘，但**真钱实盘不在当前计划**）
- **策略形态**：规则化 / ML 因子 / **LLM 自创策略**全部支持；LLM 写完整 `Strategy`
  子类源码，经沙盒审计后回测、进化
- **使用方式**：用户通过 agent 对话完成"数据 / 研究 / 回测 / 模拟盘"全链路，
  **不必直接写代码**——但每个决策都可签名、可回放、可单测

**一句话定位**：Inalpha = 量化 agent，在审计下自我进化、在无人值守时自己交易。
它**不是**开箱即用策略平台，也不是 LangChain / AutoGen 包装。

## 顶层架构方向（决策摘要）

- **不 fork** 任何单一开源项目，拆 4 个最具代表性的 repo 学各自最强的设计
- **三层架构**：Next.js + CopilotKit（入口）→ Mastra / TypeScript（编排）→ Python services（内核）
- **核心服务用 Python**：data / paper（回测+模拟盘内核）/ research / factor，跨服务走 HTTP / MCP
- **护栏借鉴 Claude Code**：hooks / permissions / plan-exec / 审计签名——数据层强制 > prompt 自律

详见 [`01-architecture-overview.md`](./01-architecture-overview.md)。

## 4 个参考 repo 的角色分工

| Repo | 拆解关注点 | 在 Inalpha 的落地 |
|---|---|---|
| **Nautilus** | 事件循环 / message bus / backtest=live 不变量 / 时间源抽象 | ✅ `services/paper` 内核（Clock / MessageBus / 同代码三态） |
| **vnpy** | Gateway / EventEngine / App 分包 | ✅ Gateway 抽象（模拟撮合 / 未来真实经纪商） |
| **qlib** | DatasetH / Handler / Alpha / Model pipeline | ✅ `services/factor`（Alpha101 / IC 有效性） |
| **TradingAgents** | 多 agent 角色分工 / 辩论 / 决策合成 | ✅ `services/research`（多 analyst + bull/bear 辩论） |

## 当前完成度快照（2026-06-05）

> 完整逐项见 [`04-current-state.md`](./04-current-state.md)。

| 里程碑 | 内容 | 状态 |
|---|---|---|
| D-8 | Plan/Exec 闭环 + Hooks + Permission Engine（LLM 无直下单路径） | ✅ |
| D-9 | LLM 自创策略 E1（三道沙盒 + 多目标 fitness）+ RiskEngine 接入 + 全市场交易日历 | ✅ |
| D-10 | 多市场数据：web 搜索 + 财报基本面 + 相对估值 analyst + MCP 生态兼容 | ✅ |
| D-11 | 多市场模拟盘：跨币种 cash + live runner（按行情自动跑 + 机器审批 + 决策复盘） | ✅ |
| D-11.1 / .2 | live runner 信任边界加固 + 运维收口（PnL 净口径 / TTL / build 退避） | ✅ |
| 下一 | research-hub 嵌套 supervisor（#6）/ E2 多代演化 MAP-Elites（#7） | 🔲 |

## 不做的事（边界）

- **不**做加密钱包托管 / DEX 链上签名（lab 阶段用 CEX API）
- **不**做合规牌照 / KYC / 资金接入（自用范畴）
- **不**自己撸交易所 REST/WS（用 CCXT + akshare/yfinance 等 connector）
- **不**碰真实资金——**真钱实盘不在当前计划**；当前最高到模拟盘（live runner
  跑模拟账户，订单本地撮合不发券商）
- **不**预设语言 / 市场 / 品种——面向全球用户，agent 回复用用户最近一条消息的语言

## 关键时间线（实况）

| 阶段 | 目标 | 状态 |
|---|---|---|
| Phase A–B | 文档骨架 + 4 份 repo 深度拆解 | ✅ |
| Phase C | Inalpha 自建内核架构（设计文档锁定 2026-05-21） | ✅ |
| Phase D-8~D-11.2 | Plan/Exec 护栏 → LLM 自创策略 → 多市场数据 → 多市场模拟盘 + live runner | ✅ |
| Phase E1 | LLM 自创策略 MVP（沙盒 + fitness） | ✅ |
| Phase E2+ | 多代演化（MAP-Elites / Island Model）/ research-hub | 🔲 规划中 |
