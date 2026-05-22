# Inalpha · Project Memory

> AI agent 编排 + 多 Python kernel 的量化实验框架。
> Claude Code 三层 memory 的 **project 层**——全仓库共享、入 git、clone 后自动加载。

## 1. 项目定位

- **Inalpha** = 用户对话驱动多 AI agent 完成"数据 / 研究 / 回测 / 实盘"全链路
- **是什么**：实验框架，重度借鉴 Claude Code 的 hooks / permissions / plan-exec / MCP / subagent
- **不是什么**：不是开箱即用策略平台、不是 LangChain / AutoGen 包装
- **三层架构**：Next.js + CopilotKit → Mastra 编排（TS）→ Python services（kernel）。详 `docs/01-architecture-overview.md`

## 2. 文档入口

| 文件 | 一句话 |
|---|---|
| `README.md` / `README.zh-CN.md` | 项目首页（双语） |
| `AGENTS.md` | 多 AI 工具兼容入口（Cursor / Codex / Aider / Cline / Continue） |
| `docs/00-context.md` | 项目背景、边界、不做什么 |
| `docs/01-architecture-overview.md` | 三层架构总图 |
| `docs/03-kernel-design.md` | Python services 职责 |
| `docs/04-current-state.md` | 最新进度与里程碑详情 |

> 内部设计文档与 ADR 在 `docs/miro/`（gitignored），不入开源仓库。

## 3. 当前 Phase（D-8c → D-9）

- **已起包**：`services/{data,paper,research}` + `packages/orchestration`
- **D-8a' → D-8c 闭环完成**：单 orchestrator + plan/exec 三件套 + Postgres 持久化 + 研究→策略→回测血缘链。详 `docs/04-current-state.md`
- **下一**：D-9（RiskEngine 规则化 + paper 真接入）、E1（LLM 改策略源码，详 ADR-0020）

## 4. 协作硬约束

- **品牌名**：始终大写 **Inalpha**（不是 inalpha / InAlpha / inAlpha） <!-- check-consistency: skip -->（元用法）
- **市场约束**：仅 crypto，不涉及 A 股 / 美股盘前盘后逻辑
- **命名约定**：
  - Python 包：`inalpha_<service>`（snake_case） <!-- check-consistency: skip -->（占位符）
  - tools：`<service>.<verb>` 或 `mcp__<server>__<verb>`
- **不要碰**：
  - `.mastra/`（gitignored 构建产物）
  - `docs/miro/`（gitignored 个人空间，除非明确授权）
  - `services/_shared/`（基础设施稳定层，改前谨慎评估）
- **tool description 必须三段式**："功能 + 何时用 + 何时不用 + 坑"
- **commit message**：中文 + `<type>(<scope>): <desc>`，可标 Phase D-N

## 5. 起步（clone 之后）

```bash
pnpm i && uv sync                  # 装依赖
bash scripts/dev.sh                # 起 data:8001 + paper:8002 + mastra:4111
bash scripts/check-consistency.sh  # 跨文件一致性检验
```

> 手动起 / 单服务起 / 端到端 smoke 命令：详 `AGENTS.md` §4。

## 6. Active TODO

- [ ] D-9：RiskEngine 规则化（max notional / 价格偏离）+ paper 真接入
- [ ] 运营 P1：深度博客 + Demo 录屏
- [ ] 多设备/多人协作启用 user 层 memory（`~/.inalpha/CLAUDE.md`）
- [ ] E1 进化：LLM 改策略源码 + 沙盒三道（ADR-0020 + Hermes 对照章节）
- [ ] D-11 候选：ADR-0026 Skills as Procedural Memory（Hermes 调研产出）

---

> 单文件硬上限 4000 字符（claw-code 实证）。已完成里程碑详情查 `docs/04-current-state.md` 与 `git log`，不在本文件累积。
