# Inalpha · AGENTS.md

> **多 AI 工具兼容的协作入口**。任何 AI 编程工具——Cursor / OpenAI Codex / Aider /
> Continue / Cline / Claude Code / Sourcegraph Cody——读取本文件即获得 Inalpha
> 项目的硬约束与导航。
> Claude Code 用户**额外**读 `CLAUDE.md`（项目级 memory，含 Claude Code 专属细节）。

## 1. 项目一句话定位

Inalpha = AI agent 编排 + 多 Python kernel 的**量化实验框架**，重度借鉴 Claude Code
的 hooks / permissions / plan-exec / MCP 模式。**禁商业用途**（见 LICENSE）。

## 2. 先读这些

| 文件 | 何时读 |
|---|---|
| `README.md` / `README.zh-CN.md` | 项目首页（双语） |
| `CLAUDE.md` | 用 Claude Code 时（其他工具也建议读，内容重叠 80%） |
| `docs/00-context.md` | 项目背景、边界、不做什么 |
| `docs/01-architecture-overview.md` | 三层架构总图 |
| `docs/03-kernel-design.md` | Python services 设计与职责拆分 |

> 内部设计文档、决策记录、思考过程在私人空间维护，**不入开源仓库**。

## 3. 协作硬约束（任何 AI 工具必须遵守）

- **品牌名**：始终大写 **Inalpha**（不写 inalpha / InAlpha / inAlpha） <!-- check-consistency: skip -->（元用法）
- **市场约束**：仅 crypto，**不**涉及 A 股 / 美股盘前盘后逻辑
- **命名约定**：
  - Python 包：`inalpha_<service>`（snake_case） <!-- check-consistency: skip -->（占位符不匹配白名单）
  - tools：`<service>.<verb>` 或 `mcp__<server>__<verb>`
- **不要碰**：
  - `.mastra/`（gitignored 构建产物）
  - `docs/miro/`（gitignored 个人空间）
  - `services/_shared/`（基础设施稳定层，改前先谨慎评估）
- **tool description 必须三段式**："功能 + 何时用 + 何时不用 + 坑"
- **commit message**：中文 + `<type>(<scope>): <desc>`，可标 Phase D-N

## 4. 起步（clone 之后）

```bash
pnpm i                                  # Node 包（packages/orchestration）
uv sync                                 # Python 包（services/*）

# 起 services（分别开 terminal）
cd services/data  && uv run python -m inalpha_data.main
cd services/paper && uv run python -m inalpha_paper.main
cd packages/orchestration && pnpm dev   # mastra dev

# 跨文件一致性检验（提交前跑一次）
bash scripts/check-consistency.sh
```

## 5. 各工具的额外建议

- **Claude Code**：本文件 + `CLAUDE.md` 同时加载；`.claude/settings.local.json`
  是个人配置（不入 git）
- **Cursor**：本文件是 `.cursorrules` 等价物；也可在 `.cursor/rules/` 下 link
- **OpenAI Codex / GitHub Codex CLI**：`AGENTS.md` 是它默认查找的标准位置
- **Aider**：`aider --read AGENTS.md` 启动
- **Continue / Cline**：把本文件路径加入 system prompt 配置
- **GitHub Copilot**：考虑同时维护 `.github/copilot-instructions.md`（短版本指向此文件）

## 6. 当前 Phase 状态

Phase **D-8a**：Plan/Exec in-memory 闭环 + orchestrator/trader/risk 三 agent +
hooks + permissions 已落地；下一里程碑 D-8b（trade_plans / approval_tokens
Postgres 持久化）/ D-9（RiskEngine 规则化 + paper-service 真接入）。
详见 [`docs/04-current-state.md`](docs/04-current-state.md) / `CLAUDE.md` §3 /
仓库根 `README.md`。

> Phase 状态可能漂移——以 `scripts/check-consistency.sh` 的检查结果为准。

## 7. 该往哪里改（任务路由）

| 想做的事 | 去哪里 |
|---|---|
| 加新策略 | `services/paper/src/inalpha_paper/strategies/` |
| 加新 tool | `packages/orchestration/src/tools/` |
| 调整内核 | `services/_shared/` 之外的 services 模块 |
| 不确定 | 先开 issue 讨论，再动 |

## 8. 红线（任何 AI 工具都不能跨）

- ❌ 不绕过设计决策直接改架构（先讨论再动）
- ❌ 不 commit `.mastra/` / `.env` / `node_modules/` / `docs/miro/` / 任何 secret <!-- check-consistency: skip -->
- ❌ 不在 `services/_shared/` 加项目特有逻辑（破坏复用）
- ❌ 不写跳过测试 / 跳过 hook 的 commit（`--no-verify` 等）——遇阻先 ask user
- ❌ 不商业使用本仓库代码（LICENSE: PolyForm Noncommercial 1.0.0）

---

> 本文件是协议入口，**短小**为美。详细规则在公开的 `docs/00-03` 与 `docs/brand/`。
