# @quant-lab/orchestration

Mastra 编排层 —— 把后端 service 包装成 LLM agent 能调用的 tool。

## D-7 范围（当前轮）

- ✅ HTTP client 封装（调 `services/data` + `services/paper`）
- ✅ Tool 层（5 个：`data.get_bars` / `data.backfill_bars` / `paper.list_strategies` /
  `paper.run_backtest` / `paper.health`）
- ✅ JWT 工具（mint / verify）
- ✅ Vitest 单测 + CLI smoke test（真服务 e2e）

后续：

- D-8：起 Mastra `Agent` 实例，挂载 tool；接 CopilotKit / AG-UI 给前端用
- D-8+：[ADR-0010 hooks](../../docs/decisions/0010-orchestration-hooks.md) /
  [ADR-0011 permissions](../../docs/decisions/0011-permission-rules.md) /
  [ADR-0012 plan-exec](../../docs/decisions/0012-plan-exec-separation.md) 落地
- D-9+：[ADR-0014 prompt cache](../../docs/decisions/0014-prompt-cache-engineering.md) /
  [ADR-0015 telemetry](../../docs/decisions/0015-agent-telemetry-standard.md)
- D-10+：[ADR-0009 MCP](../../docs/decisions/0009-mcp-as-tool-protocol.md) 接 broker

## 开发

前置：

```bash
# 1. 起 docker + 跑 alembic（D-1）
cd infra && docker compose up -d && cd migrations && uv sync && uv run alembic upgrade head

# 2. 起两个 Python service
cd services/data  && uv sync && uv run uvicorn quant_lab_data.main:app  --port 8001 &
cd services/paper && uv sync && uv run uvicorn quant_lab_paper.main:app --port 8002 &
```

然后：

```bash
cd packages/orchestration
cp .env.example .env   # JWT_SECRET 必须和服务端一致
pnpm install
pnpm test              # vitest 单测（mock fetch）
pnpm typecheck         # tsc --noEmit
pnpm smoke             # 真服务 e2e：backfill → run_backtest → 打印报告
```

## 设计原则

- **薄包装**：tool = HTTP client 调用 + Zod schema 校验，**不带业务逻辑**
- **JWT 透传 / 服务签名两种模式**：用户对话场景 forward 用户 token；后台任务 / cron
  用 `mintServiceToken()` 自签
- **错误码透传**：上游 `{code, message, details}` 原样回给 LLM，让模型基于错误码决策
- **Tool description 写"何时用 / 何时不用 / 坑"**（详见
  [docs/05-tool-skill-discipline.md](../../docs/05-tool-skill-discipline.md)）
