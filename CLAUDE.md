# Inalpha · Project Memory

> AI agent 编排 + 多 Python kernel 的量化实验框架。
> 本文件是 Claude Code 三层 memory 中的 **project 层**——全仓库共享、入 git、
> 团队成员 clone 后开 Claude Code 即自动加载。

## 1. 项目定位

- **Inalpha** = 用户对话驱动多 AI agent 协作完成"数据 / 研究 / 回测 / 实盘"全链路
- **是什么**：实验性研究框架，重度借鉴 Claude Code 的 hooks / permissions / plan-exec / MCP / subagent 模式
- **不是什么**：不是开箱即用的策略平台、不是 LangChain / AutoGen 包装
- **三层架构**：Next.js + CopilotKit（前端）→ Mastra 编排（TS）→ Python services（kernel）。详见 `docs/01-architecture-overview.md`

## 2. 文档入口

| 文档 | 一句话 |
|---|---|
| `docs/00-context.md` | 项目背景、边界、不做什么 |
| `docs/01-architecture-overview.md` | 三层架构 + 数据流总图 |
| `docs/02-agent-orchestration.md` | agent 拓扑 / workflow / skill / 模型异构 |
| `docs/03-kernel-design.md` | Python services 设计与职责拆分 |
| `docs/04-claude-code-borrowed-patterns.md` | 借鉴 Claude Code 模式综述 |
| `docs/05-tool-skill-discipline.md` | tool 命名 / schema / description 三段式 |
| `docs/06-factor-discovery-l0.md` | 因子发现 L0 实施手册 |
| `docs/10-strategy-evolution-roadmap.md` | 策略进化 E1 路线图 |
| `docs/decisions/` | 24 份 ADR（架构决策记录） |
| `docs/refs/` | 横向参考 repo 拆解 |

## 3. ADR 索引

**编排层**：0001 Mastra · 0002 REST+WS

**Claude Code 借鉴模式**：
- 0009 MCP · 0010 hooks · 0011 permissions · 0012 plan-exec
- 0013 stale state · 0014 prompt cache · 0015 telemetry · 0016 recovery
- 0017 隔离沙盒 · 0018 askUserChoice · 0021 deferred tool · 0022 slash
- 0023 statusline · 0024 session DAG

**研究 / 进化**：0019 因子发现 4 层 · 0020 策略进化 4 层（LLM 改写源码）

**其他**：0003 时序 DB · 0004 kernel 语言 · 0005 swarm worker

## 4. 当前 Phase（D-7）

- 已起包：`services/data`（CCXT Binance + Postgres）、`services/paper`（回测 + SMA cross / BuyAndHold / 布林带 MeanReversion）、`packages/orchestration`（Mastra tool 层骨架）
- 下一里程碑：D-8 trader agent + Plan/Exec 端到端、D-9 risk agent

## 5. 协作硬约束

- **品牌名**：始终大写 **Inalpha**（不是 inalpha / InAlpha / inAlpha） <!-- check-consistency: skip -->（元用法）
- **市场约束**：仅 crypto，不涉及 A 股 / 美股盘前盘后逻辑
- **命名约定**：
  - Python 包：`inalpha_<service>`（snake_case） <!-- check-consistency: skip -->（占位符）
  - tools：`<service>.<verb>` 或 `mcp__<server>__<verb>`
- **不要碰**：
  - `.mastra/`（gitignored 构建产物）
  - `services/_shared/`（基础设施稳定层，改前先 ADR）
  - **Accepted 状态的 ADR 不要绕过**——先开新 ADR supersede
- **tool description 必须三段式**："功能 + 何时用 + 何时不用 + 坑"（`docs/05` §10）
- **commit message**：中文 + `<type>(<scope>): <desc>`，可标 Phase D-N

## 6. 起步（clone 之后）

```bash
pnpm i                                  # Node 包
uv sync                                 # Python 包

# 起 services（分别开 terminal）
cd services/data  && uv run python -m inalpha_data.main
cd services/paper && uv run python -m inalpha_paper.main
cd packages/orchestration && pnpm dev   # mastra dev

# 端到端 smoke test
# TODO: 一条最小命令验证整链通
```

## 7. 本文件 TODO

- [ ] 填回 §6 的 smoke test 最小命令
- [ ] D-8 完成后更新 §4 Phase 状态
- [ ] 多设备 / 多人协作时启用 user 层 memory（`~/.inalpha/CLAUDE.md`，见 `docs/02` §Memory 层级）
- [ ] 多 broker 子账户时启用 account 层 memory

---

> 单文件硬上限 4000 字符（claw-code 实证）。新内容请先评估是否拆到 `docs/`——
> 本文件只放"Claude 第一眼必须知道的"。
