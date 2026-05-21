# inalpha

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
| B-1 | Nautilus 深度拆解（§3-§8） | ✅ 完成（commit de06798） |
| B-2 | vnpy 深度拆解（§3-§8） | ✅ 完成 |
| B-3 | qlib 深度拆解（§3-§8） | ✅ 完成 |
| B-4 | TradingAgents 深度拆解（§3-§8） | ✅ 完成 |
| C | inalpha 自建内核架构设计 | ✅ 完成 |
| C+ | 编排底盘补强（借鉴 Claude Code：hooks / permissions / plan-exec / MCP） | ✅ 完成 |
| D | infra + services/data + services/paper 骨架 | ⏳ 待启动 |
| E | MVP 端到端（用户对话 → 研究 → 回测 → 模拟盘） | ⏳ 待启动 |
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
├── 00-context.md                          → 项目背景、目标、关键决策摘要
├── 01-architecture-overview.md            → 顶层架构草图（Phase A 快照，看 03 为准）
├── 02-agent-orchestration.md              → Agent 拓扑 / 并行模型 / Swarm 设计 / Skill 取舍
├── 03-kernel-design.md                    → ⭐ 正式内核架构设计 + MVP 范围 + 接口签名
├── 04-claude-code-borrowed-patterns.md    → ⭐ Phase C+ 借鉴 Claude Code 的设计索引
├── 05-tool-skill-discipline.md            → Tool / Skill 设计纪律（命名 / description / schema）
├── 06-factor-discovery-l0.md              → ⭐ L0 因子发现实施手册（Phase F 起包用）
├── refs/
│   ├── _template.md                       → 8 段拆解模板
│   ├── nautilus.md                        → Nautilus 拆解（学事件循环 / Clock）
│   ├── vnpy.md                            → vnpy 拆解（学 Gateway / OffsetConverter）
│   ├── qlib.md                            → qlib 拆解（学算子 DSL / Pipeline）
│   └── tradingagents.md                   → TradingAgents 拆解（学多 agent + Mastra 重写映射）
└── decisions/
    ├── 0001-mastra-orchestration.md       → 编排层选型
    ├── 0002-cross-service-communication.md → 跨服务通信 HTTP+WS
    ├── 0003-timeseries-db.md              → 时序数据库 Postgres+TimescaleDB
    ├── 0004-kernel-language.md            → 内核语言 MVP Python + 后期 Rust
    ├── 0005-swarm-worker-pool.md          → Swarm worker 池放各 engine 服务内
    ├── 0009-mcp-as-tool-protocol.md       → 🆕 MCP 作为可插拔 tool 协议叠加在 REST+WS 之上
    ├── 0010-orchestration-hooks.md        → 🆕 编排 hooks 层（PreToolUse / PostToolUse / SessionStart）
    ├── 0011-permission-rules.md           → 🆕 声明式 permission（allow / ask / deny + 参数粒度）+ disable-model-invocation 补丁
    ├── 0012-plan-exec-separation.md       → 🆕 实盘交易 Plan/Exec 分离 + delegation hop 补丁
    ├── 0013-stale-state-detection.md      → 🆕 共享状态 CAS / generation / data_epoch
    ├── 0014-prompt-cache-engineering.md   → 🆕 prompt cache 工程化（cache_control + TTL + 命中率）
    ├── 0015-agent-telemetry-standard.md   → 🆕 agent 树形 trace + 6 类标准事件
    ├── 0016-recovery-recipes.md           → 🆕 按根因分类的失败恢复策略
    ├── 0017-isolation-and-sandboxing.md   → 🆕 隔离与沙盒策略（4 层分级，回答"需要沙盒吗"）
    ├── 0018-ask-user-question-as-tool.md  → 🆕 把"问用户"做成 tool call，结构化交互
    └── 0019-agent-driven-factor-discovery.md → 🆕 Agent 驱动的因子发现框架（4 层渐进 L0→L1→L2→L3）
```

> 编号 0006-0008 预留给后续 ADR（风控规则 spec / Memory schema / Agent prompt 版本管理），
> 见 [03-kernel-design.md §后续 ADR 待写](docs/03-kernel-design.md#后续-adr-待写)。
>
> ADR-0009 到 0018 是 Phase C+（编排底盘补强）批次，借鉴 Anthropic Claude Code +
> `ultraworkers/claw-code` 的生产实现，索引见
> [04-claude-code-borrowed-patterns.md](docs/04-claude-code-borrowed-patterns.md)。
>
> ADR-0019 + docs/06 是因子发现框架批次，回答"如何用 agent 探究新因子"。L0 在 Phase F
> 起包时直接落地（4.5 周）；L1/L2/L3 按触发条件后续展开。

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
