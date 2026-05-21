# 00 · 项目背景与决策

## 项目目标

自建一套**全球市场量化交易系统**，关键约束：

- **市场覆盖**：加密货币（CEX + DEX）/ 美股 美期 美期权 / A股 港股 国内期货 / 外汇 CFD
- **完整链路**：回测 + 模拟盘 + 实盘共用同一份策略代码
- **策略形态**：规则化 / ML 因子 / 做市高频 / **LLM agent 驱动**全部支持
- **使用方式**：用户通过 agent 对话调用功能，**不必直接写代码**就能跑回测、上模拟盘、
  看持仓收益（极致目标）

## 顶层架构方向（决策摘要）

- **不 fork** 任何单一开源项目，拆 4 个最具代表性的 repo 学其各自最强的设计
- **核心服务用 Python（必要时关键路径 Rust）**：数据 / 回测 / 实盘 / 因子 / 研究
- **编排层用 Mastra（TypeScript）**：每个核心服务的能力封装成 Mastra tool
- **入口层 Next.js + CopilotKit**：浏览器对话 UI 直接由本项目托管
- **独立仓库**：本项目自成体系，跨服务通过 HTTP / MCP 互通

详细架构见 `01-architecture-overview.md`。

## 4 个参考 repo 的角色分工

| Repo | 拆解关注点 | 决策依据 |
|---|---|---|
| Nautilus | 事件循环 / message bus / backtest=live 不变量 / 时间源抽象 | 决定 **quant-lab 内核**怎么拆 |
| vnpy | Gateway / EventEngine / App 分包 | 决定 **接交易所**怎么抽象，决定**国内市场**怎么接 |
| qlib | DatasetH / Handler / Alpha / Model / Strategy / Executor pipeline | 决定 **ML 因子 / 模型**接口 |
| TradingAgents | 多 agent 角色分工 / 辩论 / Portfolio Manager 决策合成 | 决定 **Mastra 编排层**多 agent 怎么排 |

## 不做的事（边界）

- **不**做加密钱包托管 / DEX 链上签名（lab 阶段全用 CEX API）
- **不**做合规牌照 / KYC / 资金接入相关（自用范畴）
- **不**自己撸交易所 REST/WS（用 CCXT + vnpy gateway）
- **不**在 lab 阶段碰真实资金（实盘只接通道，不入金）

## 关键时间线（参考）

| 阶段 | 目标 | 估算 |
|---|---|---|
| Phase A | 文档骨架 + 4 份 repo §1-§2 | 当天 |
| Phase B | 4 份 repo 深度拆解（§3-§8） | 每 repo 1-2 天 |
| Phase C | quant-lab 自建内核架构（设计文档） | 2-3 天 |
| Phase D | Mastra 编排层骨架 + 第一个 tool | 1-2 天 |
| Phase E | MVP：crypto 单交易所，规则化策略，回测→模拟盘对话调用 | 1-2 周 |

## 来源信息

- 原 plan 文件（不进 repo）：`/Users/mirror/.claude/plans/nifty-sparking-hollerith.md`
- 第一轮调研：4 个 repo 的 2026-05 活跃度校验已完成，见各 ref 文档 §1
