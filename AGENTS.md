# Inalpha · AGENTS.md

> **多 AI 工具兼容的协作入口**。任何 AI 编程工具——Cursor / OpenAI Codex / Aider /
> Continue / Cline / Claude Code / Sourcegraph Cody——读取本文件即获得 Inalpha
> 项目的硬约束与导航。
> Claude Code 用户**额外**读 `CLAUDE.md`（项目级 memory，含 Claude Code 专属细节）。

## 1. 项目一句话定位

Inalpha = AI agent 编排 + 多 Python kernel 的**量化实验框架**：agent 自己挑有效因子择时、写完整策略代码、在审计下自我进化，下单必经机器审批（LLM 不直连）；研究可叫"投资大师团"辩论，外加一个硬隔离于决策的狐神签彩蛋。工程模式重度借鉴 Claude Code
的 hooks / permissions / plan-exec / MCP。**采用 AGPL-3.0**（见 LICENSE）：随便用，但魔改后做托管服务必须公开源码。

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

# 配置统一 .env（所有 service 共享根目录一份 .env）
cp .env.example .env                    # 在 .env 里填 LLM_PROVIDER + 对应 *_API_KEY
                                        # 详见 README.md §Quick Start 的 provider/model 表

# DB schema 升到最新（dev.sh 不会自动跑；漏跑会导致 paper 服务 500：表不存在）
cd infra/migrations && uv run alembic upgrade head && cd ../..

# 一键起所有 service（推荐）
bash scripts/dev.sh                     # data:8001 + paper:8002 + research:8003 + mastra:4111
bash scripts/dev.sh logs                # 跟随日志
bash scripts/dev.sh stop                # 停止全部

# 手动起（如果想要 4 个独立 terminal）
cd services/data     && uv run uvicorn inalpha_data.main:app     --port 8001 --reload
cd services/paper    && uv run uvicorn inalpha_paper.main:app    --port 8002 --reload
cd services/research && uv run uvicorn inalpha_research.main:app --port 8003 --reload
cd packages/orchestration && pnpm dev

# 操作者控制台（apps/dashboard）—— 推荐的功能主入口，只读运行时看板
# 组合 / Live Runner / Agent 活动 / 策略实验室 / 因子库 / 风控；黑白双主题 + en/中
# 直接读根 .env（service URL + JWT_SECRET 继承），后端起着即可连
cd apps/dashboard && pnpm i && pnpm dev    # → http://localhost:3001
# 设计语言见 apps/dashboard/design.md；agent 对话功能后续也会并入控制台

# 跨文件一致性检验（提交前跑一次）
bash scripts/check-consistency.sh

# D-9 定时 agent 模式（默认关，需 SCHEDULER_ENABLED=true 起 mastra）
cd packages/orchestration
pnpm scheduler:trigger --list                  # 列全部 jobs
pnpm scheduler:trigger daily_btc_deep_dive     # 手动触发一次
# admin 页：直接 open scripts/scheduler-admin.html（默认连 4111）

# D-9 LLM 自创策略 E1 MVP（orchestrator 内置策略不够用时自动走）
# 链路：research.deep_dive → compose_strategy（拒绝时） → paper.author_strategy(code=...)
#       → paper.run_backtest(candidateId=...) → fitness 排序 → paper.promote_candidate
#         （permission ask · 用户在对话里二次确认）→ paper.start_strategy 按行情自动跑（D-11）
# 入口：services/paper/src/inalpha_paper/strategy_authoring/（三道沙盒 + fitness）
#       packages/orchestration/src/tools/strategy.ts（4 个 tool：author / list / get / promote）
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

Phase **D-11**（多市场模拟盘）已落地：单 orchestrator + plan/exec 三件套
（create_plan / approve_plan / execute_plan）+ hooks + permissions deny +
approval_token 状态机（D-8/D-9）→ LLM 自创策略沙盒 + 风控引擎 + 多市场数据
（D-9/D-10）→ 跨币种 cash + **live runner**（promoted 候选按行情自动跑，机器审批
走护栏内 plan/exec）。D-11.1 收口了 live runner 的信任边界与健壮性
（candidate 归属校验 / per-account run 上限 / 错误可重试分类）；D-11.2 收口运维（PnL 净口径扣手续费 / 运行时长 TTL auto-stop / build 退避 + 错误分类）。factor 库（services/factor:8004，pandas-ta/Alpha101/qlib + IC 有效性）已落地、策略族扩到 6。
下一里程碑：research-hub 嵌套 supervisor（issue #6）/ E2 多代演化（issue #7）。
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
- ❌ 不在不公开源码的前提下把 Inalpha（或其修改版）当作网络服务对外提供（LICENSE: AGPL-3.0；需闭源 / 托管 SaaS 请提 issue 谈双重许可）
- ❌ 多租户上线前不验证 promote 审批已按用户隔离：现 askCache 在缺稳定会话 id 时落 `__global__`（单租户兜底），需先让 Mastra 把 `threadId`/`resourceId` 注入 tool ctx，否则 A 的审批会被 B 复用（越权）。详 #91 / `hooks/with-hooks.ts` 注释

---

> 本文件是协议入口，**短小**为美。详细规则在公开的 `docs/00-03` 与 `docs/brand/`。
