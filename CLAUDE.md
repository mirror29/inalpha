# Inalpha · Project Memory

> AI agent 编排 + 多 Python kernel 的量化实验框架。
> Claude Code 三层 memory 的 **project 层**——全仓库共享、入 git、clone 后自动加载。

## 1. 项目定位

- **Inalpha** = 用户对话驱动多 AI agent 完成"数据 / 研究 / 回测 / 实盘"全链路；借鉴 Claude Code 的 hooks / permissions / plan-exec / MCP / subagent
- **不是**开箱即用策略平台 / LangChain / AutoGen 包装
- **三层**：Next.js + CopilotKit → Mastra（TS）→ Python services。详 `docs/01-architecture-overview.md`

## 2. 文档入口 & 当前 Phase（D-9）

- `README.md` / `README.zh-CN.md` 首页；`AGENTS.md` 多工具入口；`docs/00-context.md` 背景 / `01-architecture-overview.md` 架构 / `03-kernel-design.md` services / `04-current-state.md` 进度
- 内部 ADR 在 `docs/miro/`（gitignored，公开文档勿引用）
- D-8a'~D-8c 闭环；D-9 多 venue + 5 类资产 multi-market；下一 RiskEngine / E1 LLM 改策略

## 3. 协作硬约束

- **面向全球用户**：不预设语言 / 市场 / 品种；agent 回复始终用**用户最近一条消息的语言**（prompt 写死中英文 = bug）
- **市场覆盖（D-9）**：crypto + 美股 + A股 + 港股 + 日韩澳印巴英德等单股 + 全球指数 + FRED 宏观；venue 路由由 orchestrator 按"市场分类"自动选（详 `packages/orchestration/src/mastra/agents/orchestrator.ts`）
- **命名**：Python 包 `inalpha_<service>` snake_case；tools `<service>.<verb>` 或 `mcp__<server>__<verb>`
- **不要碰**：`.mastra/` / `docs/miro/` gitignored / `services/_shared/` 基础设施（改前评估）
- **tool description 三段式**：功能 + 何时用 + 何时不用 + 坑
- **commit**：中文 + `<type>(<scope>): <desc>`，可标 Phase D-N
- **git 协作**：详 `.github/CONTRIBUTING.md`

### 3.1 金融时效性硬约束（D-9 · 全 service 必守）

Inalpha 是**金融 agent**——任何"看起来很新但其实 stale"的输出都是 bug。

- 读 K 线 / 行情 / 新闻：`DataClient.get_bars` 默认 `fresh=True`（先 `/backfill/bars` 再 `/bars`）；历史回测显式 `fresh=False` 并写明原因
- 判 freshness **看 `bars[-1].ts` 距 as_of 的间隔**，不要看 bar 数量（5 根可以全是上周的）
- prompt 引用日期 / 数值 / 事件结论必须有数据源；`_MACRO_CALENDAR` 等只算"事件名 + 日期"，禁 LLM 展开成具体结论
- agent 输出回测区间必须到当前；拿不到最新时**显式说明** "数据截止 X，距 as_of N 天"
- 新加 connector 必须考虑 freshness 默认（金融默认 fresh=True）

### 3.2 Prompt / Agent 工程纪律（D-9 起·硬性）

- **不预设具体输入示例**：触发条件按**意图模式**描述，**不要**写"用户说 'BTC 能买吗'"这种锁死预期；全球用户问任何 ticker 都应能处理
- **示例只作格式参考**：venue/symbol 表里的具体 ticker 必须标注"仅供识别格式，不是预设用户会问这些"
- **as_of vs 训练 cutoff**：LLM analyst prompt 必须强调"as_of 是真现在，不要用过时具体预测当现在"

## 4. CI 红线（push 前本地必跑，缺一不可）

- `pnpm typecheck && pnpm vitest run`（orchestration）+ `uv run ruff check .`（data/paper/research）+ `bash scripts/check-consistency.sh`
- **加 import 必同步 `git add`**——`grid-size-cap.ts` / `scheduler/` / `_base.py` 漏 add 反复让 CI 挂；commit 前 `git status` 看 untracked
- 公开文档（README / AGENTS / `docs/00-04`）禁引用 `docs/miro/` 私有路径
- 模块顶层 eager 调 `getSettings()` 的入口，测试靠 vitest `setupFiles`（`tests/setup.ts`）注入默认 env

## 5. 起步 + Active TODO

```bash
pnpm i && uv sync && bash scripts/dev.sh   # data:8001 + paper:8002 + mastra:4111
```

D-9：E1 LLM 改策略（ADR-0020）；live runner（issue #1）；askUserChoice（issue #2）

---

> 单文件硬上限 4000 字符。里程碑详情查 `docs/04-current-state.md` 与 `git log`。
