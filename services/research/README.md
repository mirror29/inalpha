# services/research

LLM 多 agent 决策（TradingAgents 风格）—— D-8b 起步。

## 当前能力

| Endpoint | 用途 |
|---|---|
| `GET /health` | 存活探活，返当前 LLM provider |
| `POST /deep_dive` | 跑一次完整研究链路：2 个 analyst 并行 + research manager 综合 → `ResearchPlan` |

## 架构

```
DeepDiveRequest
   ↓
analysts/{technical,fundamental}  (asyncio.gather 并行)
   ↓ AnalystBrief x N
ResearchManager.synthesize()
   ↓
ResearchPlan
```

- `technical`：吃 K 线 + 简单指标（SMA / RSI / 涨跌幅），LLM 出短期立场
- `fundamental`：D-8b LLM-only（无外部数据），出中长期 thesis；D-9+ 接 sentiment / news
- `manager`：LLM 综合 → rating / thesis / risks / suggested_action / horizon

LLM 抽象：

- `DeepSeekLLMClient`：走 OpenAI 兼容 API（DeepSeek 同协议）
- `FakeLLMClient`：测试 mock，按 system prompt 子串选预设响应

## 开发

```bash
cd services/research
cp .env.example .env  # 至少配 LLM_API_KEY（DeepSeek key）
uv sync --group dev
uv run pytest              # 全部 fake LLM，不烧 token
uv run uvicorn inalpha_research.main:app --reload --port 8003
```

测真 LLM（烧 token）：

```bash
LLM_PROVIDER=deepseek LLM_API_KEY=sk-xxx \
  uv run pytest -m integration   # D-9 起加 integration mark
```

## 接 orchestration

`packages/orchestration` 通过 `research.deep_dive` tool 调本服务的 POST /deep_dive。
JWT 透传走 `inalpha_shared.auth`，跟 data / paper 同套机制。

## 后续 D-9+

- 加 sentiment / news analyst（接 X / Reddit / RSS）
- bull vs bear 辩论（Mastra workflow `.dowhile`）
- LLM 调用缓存（ADR-0014 prompt cache）
- 真实成本 / token 计数 telemetry（ADR-0015）
