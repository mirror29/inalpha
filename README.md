# quant-lab

自建全球市场量化交易系统的学习与开发项目。

## 目标

- **市场覆盖**：加密货币（CEX/DEX）/ 美股 美期 美期权 / A股 港股 国内期货 / 外汇 CFD
- **完整链路**：回测 → 模拟盘 → 实盘
- **策略形态**：规则化 / ML 因子 / 做市高频 / LLM agent 驱动
- **顶层定位**：核心模块作为基础能力，通过 **Mastra 框架** 编排，用户通过 agent
  对话直接调用

## 当前状态

| Phase | 内容 | 状态 |
|---|---|---|
| A | 项目骨架 + 4 份 repo 拆解的索引页（§1-§2） | ✅ 完成 |
| B-1 | Nautilus 深度拆解（§3-§8） | ⏳ 待启动 |
| B-2 | vnpy 深度拆解（§3-§8） | ⏳ 待启动 |
| B-3 | qlib 深度拆解（§3-§8） | ⏳ 待启动 |
| B-4 | TradingAgents 深度拆解（§3-§8） | ⏳ 待启动 |
| C | quant-lab 自建内核架构设计 | ⏳ 待启动 |
| D | Mastra 编排层 + agent 对话入口 | ⏳ 待启动 |
| E | 第一个端到端 MVP（建议先做 crypto 单交易所） | ⏳ 待启动 |

## 4 个参考 repo

| Repo | 学什么 | 拆解文档 |
|---|---|---|
| [nautechsystems/nautilus_trader](https://github.com/nautechsystems/nautilus_trader) | Rust + Python 现代事件驱动；backtest = live 不变量 | [docs/refs/nautilus.md](docs/refs/nautilus.md) |
| [vnpy/vnpy](https://github.com/vnpy/vnpy) | Gateway 抽象 + 国内市场覆盖（CTP/XTP） | [docs/refs/vnpy.md](docs/refs/vnpy.md) |
| [microsoft/qlib](https://github.com/microsoft/qlib) | ML 因子 → 模型 → 组合 pipeline | [docs/refs/qlib.md](docs/refs/qlib.md) |
| [TauricResearch/TradingAgents](https://github.com/TauricResearch/TradingAgents) | 多 LLM agent 角色分工 + 辩论决策 | [docs/refs/tradingagents.md](docs/refs/tradingagents.md) |

## 文档导航

```
docs/
├── 00-context.md                  → 项目背景、目标、关键决策摘要
├── 01-architecture-overview.md    → 自建系统顶层架构草图（含 Mastra 编排层）
├── refs/
│   ├── _template.md               → 8 段拆解模板
│   ├── nautilus.md                → Nautilus 拆解
│   ├── vnpy.md                    → vnpy 拆解
│   ├── qlib.md                    → qlib 拆解
│   └── tradingagents.md           → TradingAgents 拆解
└── decisions/
    └── 0001-mastra-orchestration.md → ADR：为什么用 Mastra 编排
```

## 后续目录（Phase C+ 才建）

```
apps/         Next.js + CopilotKit 前端
packages/     Mastra agents / tools / workflows（TypeScript）
services/     data / backtest / live / factor / research（Python + Rust）
```

## 不在本仓库做的事

- LLM 推理：独立部署或对接外部 LLM provider（OpenAI / Anthropic / DeepSeek / ...）
- 历史数据存储：建议 QuestDB 或 ClickHouse，单独跑容器
- 实盘资金：不在 lab 阶段碰，全部用模拟盘
