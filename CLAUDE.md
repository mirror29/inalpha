# Inalpha · Project Memory

> AI agent 编排 + 多 Python kernel 的量化实验框架。
> 本文件是 Claude Code 三层 memory 中的 **project 层**——全仓库共享、入 git、
> 团队成员 clone 后开 Claude Code 即自动加载。

## 1. 项目定位

- **Inalpha** = 用户对话驱动多 AI agent 协作完成"数据 / 研究 / 回测 / 实盘"全链路
- **是什么**：实验性研究框架，重度借鉴 Claude Code 的 hooks / permissions / plan-exec / MCP / subagent 模式
- **不是什么**:不是开箱即用的策略平台、不是 LangChain / AutoGen 包装
- **三层架构**：Next.js + CopilotKit（前端）→ Mastra 编排（TS）→ Python services（kernel）
  详见 `docs/01-architecture-overview.md` 与 `docs/03-kernel-design.md`

## 2. 文档入口

| 文件 | 一句话 |
|---|---|
| `README.md` / `README.zh-CN.md` | 项目首页（双语） |
| `AGENTS.md` | 多 AI 工具兼容入口（Cursor / Codex / Aider / Cline / Continue） |
| `docs/00-context.md` | 项目背景、边界、不做什么 |
| `docs/01-architecture-overview.md` | 三层架构总图 |
| `docs/03-kernel-design.md` | Python services 设计与职责拆分 |

> 内部设计文档、决策记录、思考过程留在私人空间，不入开源仓库。

## 3. 当前 Phase（D-8a'）

- 已起包：`services/data`（CCXT + Postgres）、`services/paper`（回测 + 3 策略 + `/orders/submit`）、`packages/orchestration`（Mastra tool + hooks + permissions + plan store + 单 orchestrator）
- **D-8a' 完成**：单 orchestrator + plan/exec 三件套（`create_plan`→`approve_plan`→`execute_plan`），三层中间件保证 "LLM 无直接下单"：状态机 + 一次性 approval_token + permissions deny `paper.submit_order*`。multi-agent 立场对抗的故事留给 `services/research`（Phase E+）
- 下一里程碑：D-8b（trade_plans / approval_tokens Postgres 持久化）、D-9（RiskEngine 规则化：max notional / 价格偏离）

## 4. 协作硬约束

- **品牌名**：始终大写 **Inalpha**（不是 inalpha / InAlpha / inAlpha） <!-- check-consistency: skip -->（元用法）
- **市场约束**：仅 crypto，不涉及 A 股 / 美股盘前盘后逻辑
- **命名约定**：
  - Python 包：`inalpha_<service>`（snake_case） <!-- check-consistency: skip -->（占位符）
  - tools：`<service>.<verb>` 或 `mcp__<server>__<verb>`
- **不要碰**：
  - `.mastra/`（gitignored 构建产物）
  - `docs/miro/`（gitignored 个人空间）
  - `services/_shared/`（基础设施稳定层，改前先谨慎评估）
- **tool description 必须三段式**："功能 + 何时用 + 何时不用 + 坑"
- **commit message**：中文 + `<type>(<scope>): <desc>`，可标 Phase D-N

## 5. 起步（clone 之后）

```bash
pnpm i                                  # Node 包
uv sync                                 # Python 包

# 一键起所有 service（推荐）
bash scripts/dev.sh                     # data:8001 + paper:8002 + mastra:4111

# 手动起：见 AGENTS.md §4 的 3-terminal 写法

# 跨文件一致性检验
bash scripts/check-consistency.sh
```

## 6. 本文件 TODO

- [ ] 填回 §5 端到端 smoke test 最小命令
- [x] D-8a 完成（2026-05-21）：Plan/Exec in-memory + agent 三分
- [x] D-8a' refactor（2026-05-22）：去 supervisor，详 §3
- [x] 运营基础设施 P0（2026-05-22）：.github + CONTRIBUTING/COC/SECURITY + dev.sh + README D-8a' 叙事
- [ ] D-8b：trade_plans / approval_tokens Postgres 表 + alembic migration
- [ ] D-9：Risk agent 规则化（max notional / 价格偏离）+ paper RiskEngine 真接入
- [ ] 运营 P1：2 篇深度博客 + Demo 录屏（依赖 D-8b 完成后再启动）
- [ ] 多设备 / 多人协作时启用 user 层 memory（`~/.inalpha/CLAUDE.md`）

---

> 单文件硬上限 4000 字符（claw-code 实证）。新内容前先评估是否拆到 `docs/` 公开文档。
