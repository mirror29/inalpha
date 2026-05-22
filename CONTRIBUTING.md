# 贡献指南

感谢有兴趣为 Inalpha 贡献！本文件**不重复**已经写在 `AGENTS.md` 和 `CLAUDE.md` 里的规则——下面只说参与流程。

> Inalpha 是实验性研究框架，处于 alpha 阶段（当前 Phase D-8a）。
> 在动手之前，强烈建议先读：[`AGENTS.md`](AGENTS.md) · [`docs/00-context.md`](docs/00-context.md) · [`docs/01-architecture-overview.md`](docs/01-architecture-overview.md)

## 1. 开始之前

- **License**：本项目采用 [PolyForm Noncommercial 1.0.0](LICENSE)。提交 PR 即表示你同意你的贡献以同样的许可证发布，且**不**用于商业用途。
- **行为准则**：见 [`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md)。
- **协作硬约束**：参见 [`AGENTS.md §3`](AGENTS.md) 与 [`AGENTS.md §8`](AGENTS.md) 红线条款。
- **安全漏洞**：**不要**开 public issue。流程见 [`SECURITY.md`](SECURITY.md)。

## 2. 该提 issue 还是 PR？

| 你想做的 | 走哪个流程 |
|---|---|
| 报 bug | 开 issue（用 bug_report 模板） |
| 提建议 / 新功能 | 开 issue（用 feature_request 模板） |
| 开放性设计讨论、"这样做对吗" | 去 GitHub Discussions，不要开 issue |
| 改 typo / 文档小修小补 | 直接开 PR |
| 涉及架构 / 跨 service 的变更 | **先**开 issue 讨论，**对齐方向后**再开 PR |

## 3. 本地起步

详见 [`AGENTS.md §4`](AGENTS.md)。最简流程：

```bash
pnpm i
uv sync
bash scripts/dev.sh           # 一键起 services + mastra dev
bash scripts/check-consistency.sh  # 提交前必须 pass
```

## 4. Commit / PR 规范

- **Commit message**：中文 + `<type>(<scope>): <desc>`，可加 Phase 标记（如 `feat(orchestration): D-8a 加 sharedMemory`）
- **Type 取值**：`feat` · `fix` · `refactor` · `docs` · `test` · `chore` · `style` · `perf`
- **Scope**：`data` · `paper` · `research` · `orchestration` · `docs` · `infra` · 或具体模块名
- **PR 模板**：见 [`.github/PULL_REQUEST_TEMPLATE.md`](.github/PULL_REQUEST_TEMPLATE.md)，所有自检项必须勾选完整
- **CI**：所有 PR 必须 CI 全绿（一致性检查 + typecheck + ruff），见 [`.github/workflows/ci.yml`](.github/workflows/ci.yml)

## 5. 代码风格

- **TypeScript / Mastra 层**：`pnpm typecheck` 必须无错。注释用 JSDoc。
- **Python services**：`uv run ruff check .` 必须 pass；`uv run mypy .` 尽力 pass。
- **Tool description 三段式**："功能 + 何时用 + 何时不用 + 坑"（[`AGENTS.md §3`](AGENTS.md)）。
- **不要**写没有 Why 的注释。well-named identifiers 已经解释了 What。

## 6. 测试

- TS 层：`pnpm test`（vitest）
- Python 层：`uv run pytest`（在各 service 目录下）
- 涉及外部依赖（CCXT / Postgres）的测试要么用 fixture，要么标 `@pytest.mark.integration` 跳过 CI。

## 7. 不接受的贡献

- 引入 A 股 / 美股 / 港股相关逻辑（仅 crypto）
- 让 LLM 获得直接下单路径的改动（破坏核心安全模型）
- 商业化 / 收费 / 引流到付费服务的代码或文档
- 引入 GPL / AGPL 等与 PolyForm Noncommercial 冲突的依赖
- "顺手清理"型大规模重构（先 issue 讨论）

## 8. 不确定？

在 GitHub Discussions 起一个 thread，或在相关 issue 下 @ 维护者。冷启动阶段，48 小时内会有首次回复。
