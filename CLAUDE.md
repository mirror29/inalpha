# Inalpha · Project Memory

> AI agent 编排 + 多 Python kernel 的量化实验框架。
> Claude Code 三层 memory 的 **project 层**——全仓库共享、入 git、clone 后自动加载。

## 1. 项目定位

- **Inalpha** = 用户对话驱动多 AI agent 完成"数据 / 研究 / 回测 / 实盘"全链路；借鉴 Claude Code 的 hooks / permissions / plan-exec / MCP / subagent
- **不是**开箱即用策略平台 / LangChain / AutoGen 包装
- **三层**：Next.js + CopilotKit → Mastra（TS）→ Python services。详 `docs/01-architecture-overview.md`

## 2. 文档入口

- `README.md` / `README.zh-CN.md` — 项目首页双语；`AGENTS.md` — 多 AI 工具入口
- `docs/00-context.md` 背景 / `docs/01-architecture-overview.md` 架构 / `docs/03-kernel-design.md` services / `docs/04-current-state.md` 进度
- 内部 ADR 在 `docs/miro/`（gitignored）

## 3. 当前 Phase（D-9）

D-8a'~D-8c 闭环完成；D-9 多 venue + 5 类资产 multi-market；下一 D-9 RiskEngine / E1 LLM 改策略。详 `docs/04-current-state.md`

## 4. 协作硬约束

- **产品定位**：面向**全球用户**的量化研究 / 实验框架。**不**预设用户语言、市场或品种。
- **市场覆盖（D-9 起）**：crypto + 美股 + A股 + 港股 + 日韩澳印巴英德等单股 + 全球指数 + FRED 宏观；venue 路由由 orchestrator 按"市场分类"自动选（详 `packages/orchestration/src/mastra/agents/orchestrator.ts`）。
- **命名**：Python 包 `inalpha_<service>` snake_case；tools `<service>.<verb>` 或 `mcp__<server>__<verb>`
- **不要碰**：`.mastra/` 构建产物 / `docs/miro/` gitignored / `services/_shared/` 基础设施（改前评估）
- **tool description 必须三段式**："功能 + 何时用 + 何时不用 + 坑"
- **commit message**：中文 + `<type>(<scope>): <desc>`，可标 Phase D-N

### 4.1 金融时效性硬约束（D-9 · 全 service 必守）

Inalpha 是**金融 agent**——任何"看起来很新但其实 stale"的输出都是 bug。

- 读 K 线 / 行情 / 新闻：`DataClient.get_bars` 默认 `fresh=True`（先 `/backfill/bars` 再 `/bars`）；历史回测显式 `fresh=False` 并写明原因
- 判 freshness **看 `bars[-1].ts` 距 as_of 的间隔**，不要看 bar 数量（5 根可以全是上周的）
- prompt 引用日期 / 数值 / 事件结论必须有数据源支撑；`_MACRO_CALENDAR` 等只算"事件名 + 日期"，禁止 LLM 展开成具体结论
- agent 输出回测区间必须到当前；拿不到最新时必须**显式说明** "数据截止 X，距 as_of N 天" 而非装作没事
- 新加 connector 必须考虑 freshness 默认（金融默认 fresh=True）

### 4.2 Prompt / Agent 工程纪律（D-9 起·硬性）

- **不预设具体输入示例**：触发条件按**意图模式**描述，**不要**写"用户说 'BTC 能买吗'"这种锁死预期；全球用户问任何 ticker 都应能处理
- **语言匹配用户**：agent 回复始终用**用户最近一条消息的语言**——任何 prompt 里写死"中文/英文回复"都视为 bug（Inalpha 面向全球）
- **示例只作格式参考**：venue/symbol 表里的具体 ticker 必须标注"仅供识别格式，不是预设用户会问这些"
- **as_of vs 训练 cutoff**：LLM analyst prompt 必须强调"as_of 是真现在，不要用过时具体预测当现在"

## 5. 起步（clone 之后）

```bash
pnpm i && uv sync                  # 装依赖
bash scripts/dev.sh                # 起 data:8001 + paper:8002 + mastra:4111
bash scripts/check-consistency.sh  # 跨文件一致性检验
```

> 手动起 / 单服务起 / 端到端 smoke 命令：详 `AGENTS.md` §4。

## 6. Active TODO

- D-9：RiskEngine 规则化 + paper 真接入；运营 P1（博客 + Demo）；E1 LLM 改策略源码（ADR-0020）；D-11 候选：ADR-0026 Skills as Procedural Memory

---

> 单文件硬上限 4000 字符（claw-code 实证）。已完成里程碑详情查 `docs/04-current-state.md` 与 `git log`，不在本文件累积。
