# @inalpha/orchestration

Mastra / TypeScript 编排层：把 Python services 和外部 MCP 包装成 agent tools，并统一执行
身份传递、permissions、hooks、plan/exec、审批、调度、skills 和可观测性。

## 当前职责

- **Agent 路由**：单个 orchestrator agent 直接调用各 tool，并按市场分类选择数据源
  （trader / risk 子 agent 已在 D-8a′ 移除；下单仍走 plan/exec，模型没有直下单 tool）。
- **Tool 族**：`data.*` / `web.*` / `factor.*` / `research.*` / `paper.*` / `trade.*` /
  `evolver.*` / `swarm.*` / `skill.*` / `risk.*` / `scheduler.*` / `divination.*` /
  `sandbox.*`，以及可插拔 `mcp__<server>__<verb>`。
- **交易护栏**：`create_plan → approve_plan → execute_plan`；计划和审批事实由 paper DB
  持久化；模型只能转交审批 tool 返回的一次性 approval token，没有直下单 tool。
- **演化入口**：`evolver.run_evolution` 在产生 LLM 费用前要求逐次授权；查询和取消 tools
  始终透传当前用户 JWT。Evolver 只产出候选，不自动 promote / start / order。
- **运行时治理**：Pre/Post tool hooks、allow/ask/deny permissions、scheduler、agent eval、
  prompt cache、trace 和领域错误码透传。

## 本地开发

推荐从仓库根启动完整依赖：

```bash
cd packages/orchestration && pnpm i && cd ../..
for service in data paper research factor evolver; do
  (cd "services/$service" && uv sync)
done
cp .env.example .env && cp infra/.env.example infra/.env
(cd infra && docker compose up -d)
(cd infra/migrations && uv sync && uv run alembic upgrade head)
bash scripts/dev.sh
```

只开发编排层时：

```bash
cd packages/orchestration
pnpm install
pnpm dev
pnpm typecheck
pnpm vitest run
```

服务地址、`JWT_SECRET`、LLM provider/model 与 `EVOLVER_SERVICE_URL` 统一从仓库根 `.env`
读取。用户对话转发用户 JWT；后台用途的 token 必须短时、用途限定并绑定必要 scope。

## Tool 设计约束

- tool 是 HTTP/MCP 的薄适配层：Zod 校验、身份传递、错误标准化，不复制 Python 业务逻辑。
- description 必须写清“功能 + 何时用 + 何时不用 + 坑”。
- 用户可见错误保留稳定 `code`，让 agent 可以分辨重试、降级与需人工处理的状态。
- 新增高风险或有费用的操作时，先设计 owner scope、幂等键、approval 与领域审计事实。
- 任何新增直下单路径、绕过 plan/exec，或让模型绕过 approve 获取 / 复用审批 token 的改动
  都不接受。

## Skills

`skills/<name>/` 使用 AgentSkills 结构（`SKILL.md` + YAML frontmatter + 可选
`references/`）。frontmatter 的 `name` 必须等于 kebab-case 目录名，`description` 按意图模式
描述且不写死触发短语。

新增或修改 skill 时：

1. 只加载 `.md/.json/.txt`；外来 skill 的 `scripts/` 不执行。
2. 改写为市场无关的方法论，数据步骤映射到现有 tools，并保留 LICENSE/ATTRIBUTION。
3. 运行 `pnpm vitest run` 与 `bash ../../scripts/check-consistency.sh`。
4. 重启 orchestration；skill manifest 在进程内缓存，热更新不会刷新清单。

更完整的架构和当前阶段见
[`docs/01-architecture-overview.md`](../../docs/01-architecture-overview.md) 与
[`docs/04-current-state.md`](../../docs/04-current-state.md)。
