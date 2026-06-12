# Contributing to Inalpha / 贡献指南

> Inalpha is an experimental research framework in **alpha**. Phase D-11 has landed (multi-market paper trading: cross-currency cash + live runner); next up are research-hub (#6) and E2 strategy evolution (#7).
> Before writing code, we strongly recommend reading: [`AGENTS.md`](AGENTS.md) · [`docs/00-context.md`](docs/00-context.md) · [`docs/01-architecture-overview.md`](docs/01-architecture-overview.md) · [`docs/04-current-state.md`](docs/04-current-state.md)
>
> Inalpha 是实验性研究框架，处于 **alpha** 阶段。Phase D-11 已落地（多市场模拟盘：跨币种 cash + live runner）；下一步是 research-hub（#6）与 E2 策略演化（#7）。
> 动手之前，强烈建议先读上述四份文档。

## 1. Before you start / 开始之前

- **License**: this project is licensed under [GNU AGPL-3.0](LICENSE). Submitting a PR means you agree your contribution ships under the same license, including the network-copyleft clause.
  **License**：本项目采用 [GNU AGPL-3.0](LICENSE)。提交 PR 即表示你同意你的贡献以同样的许可证发布（含 AGPL 的网络 copyleft 条款）。
- **Code of Conduct**: see [`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md).
  **行为准则**：见 [`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md)。
- **Hard constraints**: see [`AGENTS.md`](AGENTS.md) §3 and the §8 red lines.
  **协作硬约束**：参见 [`AGENTS.md`](AGENTS.md) §3 与 §8 红线条款。
- **Security vulnerabilities**: do **not** open a public issue — see [`SECURITY.md`](SECURITY.md).
  **安全漏洞**：**不要**开 public issue，流程见 [`SECURITY.md`](SECURITY.md)。

## 2. Issue or PR? / 该提 issue 还是 PR？

| What you want to do / 你想做的 | Route / 走哪个流程 |
|---|---|
| Report a bug / 报 bug | Issue with the bug_report template / 开 issue（bug_report 模板） |
| Suggest a feature / 提建议、新功能 | Issue with the feature_request template / 开 issue（feature_request 模板） |
| Open-ended design discussion / 开放性设计讨论、「这样做对吗」 | GitHub Discussions — not an issue / 去 Discussions，不要开 issue |
| Typo / small doc fix / 改 typo、文档小修小补 | Open a PR directly / 直接开 PR |
| Architectural / cross-service change / 架构、跨 service 变更 | Discuss in an issue **first**, PR after alignment / **先**开 issue 讨论，对齐方向后再开 PR |

## 3. Local setup / 本地起步

```bash
pnpm i && uv sync
bash scripts/dev.sh                 # data:8001 + paper:8002 + mastra:4111
bash scripts/check-consistency.sh   # must pass before committing / 提交前必须 pass
```

CI red lines — run locally before every push / CI 红线，push 前本地必跑（缺一不可）：

```bash
pnpm typecheck && pnpm vitest run   # packages/orchestration
uv run ruff check .                 # services/data | paper | research
bash scripts/check-consistency.sh
```

## 4. Branch model / 分支模型

| Branch / 分支 | Purpose / 用途 | Protection |
|---|---|---|
| `main` | Production / 生产分支 | PR + CI required; no force-push / deletion · 必走 PR + 必过 CI；禁 force push / 删除 |
| `staging` | Pre-prod canary / 灰度 | Same as `main` / 同 `main` |
| `feature/*` `fix/*` `chore/*` `test/*` `docs/*` | Task branches / 任务分支 | None / 无 |

Use short kebab-case names / 任务分支建议短命名 + kebab-case：`feature/risk-engine-v1` · `fix/paper-trade-double-fill` · `chore/dep-bump-mastra`。

## 5. When to go through staging / 何时走 staging

**Must go through staging (high-risk)** / **必经 staging（高风险）**：

- New strategy family, or large changes to `services/research/` / `services/paper/` / `packages/orchestration/`
  新策略族，或上述三个核心模块的大改
- New connector / venue / broker
  新 connector / 新 venue / 新 broker
- Large orchestrator schema or agent prompt changes
  orchestrator schema 或 agent prompt 大改
- `services/_shared/` infrastructure changes
  改 `services/_shared/` 基础设施
- Risk-control or live-runner path changes
  改风控 / live runner 链路

**Direct PR → main (low-risk)** / **直接 PR → main（低风险）**：

- Bugfix / docs / test changes · bugfix / 文档 / 测试改动
- One-off refactor / dependency bump · 一次性 refactor / 依赖升级
- Single-file / single-module local change · 单文件 / 单模块局部改动
- Workflow / CI tweaks · workflow / CI 调整

When unsure, go through staging — one extra gate is cheap.
判断不清就走 staging——多一道闸不算贵。

## 6. PR flow / PR 流程

High-risk path / 高风险路径：

```
feature/* → PR → staging → Cloudflare preview deploy → manual testing / 人工把玩 → PR staging → main
```

Normal path / 普通路径：

```
feature/* → PR → main
```

Every PR automatically triggers / 每个 PR 自动触发：

1. **CI** — 6 required status checks / 6 个必填 status check（详 [`.github/workflows/ci.yml`](.github/workflows/ci.yml)）
2. **Claude PR Review** — non-blocking auto review / 非阻塞自动审查（详 [`.github/workflows/claude-review.yml`](.github/workflows/claude-review.yml)）
3. **Cloudflare Pages preview deploy** — a unique URL per PR, posted in a comment / 每 PR 独立 URL，评论里贴出

Mentioning `@claude` in a PR / issue / review comment starts a conversation with the bot (only users with write access can trigger it; see [`.github/workflows/claude.yml`](.github/workflows/claude.yml)).
在 PR / issue / review comment 里 `@claude` 可触发对话（仅有 write 权限的用户能触发，详 [`.github/workflows/claude.yml`](.github/workflows/claude.yml)）。

## 7. Branch protection rules / 分支保护规则（当前）

`main` and `staging` are configured identically / `main` 与 `staging` 配置一致：

- **6 required CI status checks** / **必填 6 个 CI status check**：`orchestration · typecheck + test` / `web · typecheck + build` / `Cross-file consistency check / 跨文件一致性检验` / `python services · ruff + mypy (data|paper|research)`
- PR required, but `required_approving_review_count: 0` — solo project, avoids self-approval deadlock.
  必走 PR，但**不**强制 approval（单人项目，避免自批死锁）。
- `allow_force_pushes: false` · `allow_deletions: false`
- `enforce_admins: false` — admin can bypass in emergencies / admin 紧急可绕过
- Claude PR Review is **not** a required check (LLMs flake occasionally; don't block PRs on it).
  Claude PR Review **不**必填（LLM 偶尔抽风，避免卡 PR）。

When a CI job name changes, update the protection `contexts` in the same change — otherwise PRs wait forever for a check that never runs.
CI workflow 改 job 名时必须同步更新 protection 的 `contexts`，否则 PR 会一直等一个永远不跑的 check。

## 8. Commit / PR conventions / Commit 规范

- **Commit message**: Chinese, `<type>(<scope>): <desc>`; one logical change per commit — don't mix unrelated modules in one commit.
  **Commit message**：中文 + `<type>(<scope>): <desc>`；一次 commit 只做一件事，不要把不相关模块揉进同一个 commit。
- **type**: `feat` / `fix` / `refactor` / `docs` / `test` / `chore` / `style` / `perf` / `ci`
- **scope**: `data` / `paper` / `research` / `orchestration` / `web` / `docs` / `infra`, or a concrete module name / 或具体模块名
- A Phase tag is welcome / 可标 Phase D-N（例：`feat(paper): 跨币种 cash 账本 (D-11)`）
- Check untracked files before committing — a missing `git add` for a newly imported file breaks CI.
  commit 前 `git status` 检查 untracked——新 import 的实现文件漏 add 会让 CI 挂。
- **PR template**: every self-review item in [`.github/PULL_REQUEST_TEMPLATE.md`](.github/PULL_REQUEST_TEMPLATE.md) must be checked.
  **PR 模板**：自检项必须勾选完整。

## 9. Code style & tests / 代码风格与测试

- **TypeScript / Mastra**: `pnpm typecheck` must be clean; comments in JSDoc.
  **TypeScript / Mastra 层**：`pnpm typecheck` 必须无错，注释用 JSDoc。
- **Python services**: `uv run ruff check .` must pass; `uv run mypy .` best-effort.
  **Python services**：ruff 必须 pass，mypy 尽力 pass。
- **Tool descriptions** follow the three-part style: function + when to use + when not to use + gotchas ([`AGENTS.md`](AGENTS.md) §3).
  **Tool description 三段式**：功能 + 何时用 + 何时不用 + 坑。
- Don't write comments that only restate *what* — well-named identifiers already do that. Comments are for *why*.
  **不要**写没有 Why 的注释，well-named identifiers 已经解释了 What。
- **Tests**: TS `pnpm test` (vitest); Python `uv run pytest` per service. Tests hitting external deps (CCXT / Postgres) use fixtures or `@pytest.mark.integration` to skip in CI.
  **测试**：TS 层 vitest，Python 层各 service 下 pytest；涉及外部依赖的测试要么用 fixture，要么标 `@pytest.mark.integration` 跳过 CI。

## 10. Contributions we don't accept / 不接受的贡献

- Hardcoding a specific language, market, or instrument in prompts / routing — Inalpha serves global users and covers crypto, US / CN / HK equities, global single stocks, indices, and FRED macro ([`CLAUDE.md`](CLAUDE.md) §3).
  在 prompt / 路由里硬编码语言、市场、品种——Inalpha 面向全球用户，已覆盖 crypto + 美股 + A股 + 港股 + 全球单股 / 指数 + FRED 宏观。
- Anything that gives the LLM a direct order-placement path — it breaks the core safety model.
  让 LLM 获得直接下单路径的改动——破坏核心安全模型。
- Code or docs for commercialization / paywalls / funneling to paid services.
  商业化 / 收费 / 引流到付费服务的代码或文档。
- Dependencies incompatible with AGPL-3.0 (e.g. proprietary, non-redistributable libraries; GPL/AGPL-family licenses are fine).
  引入与 AGPL-3.0 不兼容的依赖（如纯专有 / 不可重分发的库；GPL/AGPL 系兼容）。
- Drive-by mass refactors — open an issue first.
  「顺手清理」型大规模重构——先开 issue 讨论。

## 11. Maintainer notes / 维护者备忘

- **Claude PR Review auth**: Max-subscription OAuth (GitHub Secret `CLAUDE_CODE_OAUTH_TOKEN`, valid ~1 year). Regenerate: local `claude setup-token` → browser auth → `gh secret set CLAUDE_CODE_OAUTH_TOKEN`. On 401, rotate; if the token was ever exposed (chat / screenshot / log), revoke and regenerate immediately.
  **CR 鉴权**：走 Max 订阅 OAuth，有效期约 1 年；重生成走 `claude setup-token`；出 401 直接换新，token 暴露过立即 revoke + 重生成。
- **Emergency hotfix** (production P0 only): admin direct-push to `main` (`enforce_admins: false` allows it — don't make it a habit), then immediately backfill a PR + incident note, and cherry-pick / merge to `staging` to prevent drift.
  **紧急 hotfix**（只在生产 P0 故障）：admin 直推 main 后立即 backfill PR + 写 incident note，并同步到 staging 防分支漂移。
- **Editing protection** / **改 protection**：

  ```bash
  gh api -X PUT /repos/mirror29/inalpha/branches/<branch>/protection \
    -H "Accept: application/vnd.github+json" \
    --input <protection.json>
  ```

- **Local data discipline** (applies to humans *and* agents): gitignored data is still an asset — "not in git" does not mean "fine to lose". Before moving / deleting / overwriting any gitignored data file (chat history, traces, dev DB), run `bash scripts/backup-data.sh` (or `cp` a copy outside the target mechanism) first. Before writing persistent data into a directory you don't fully understand, verify its lifecycle (who creates it, who cleans it, when) — `.mastra/` is a build dir that gets wiped on every `mastra dev` start. All orchestration persistent paths must go through `src/mastra/paths.ts` (enforced by check-consistency C8).
  **本地数据纪律**（对人也对 agent）：gitignored 数据也是资产——"不进 git"不等于"丢了活该"。移动 / 删除 / 覆盖任何 gitignored 数据文件（聊天历史、traces、dev 库）前，先跑 `bash scripts/backup-data.sh`（或 `cp` 到目标机制之外）。向语义不明的目录写持久数据前，先验证其生命周期（谁创建、谁清理、何时清理）——`.mastra/` 是 build 目录，每次 `mastra dev` 启动整目录清空。orchestration 持久化路径一律走 `src/mastra/paths.ts`（check-consistency C8 红线强制）。

## 12. Unsure? / 不确定？

Open a GitHub Discussions thread, or @ the maintainer in a related issue. During the cold-start phase, first response within 48 hours.
在 GitHub Discussions 起一个 thread，或在相关 issue 下 @ 维护者。冷启动阶段，48 小时内会有首次回复。
