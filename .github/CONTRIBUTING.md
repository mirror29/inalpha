# Contributing to Inalpha

> Inalpha 的 git 协作体系：分支模型、PR 流程、灰度路径、CR 鉴权、hotfix。
> 配套 CLAUDE.md §3（协作硬约束）+ §4（CI 红线）。

## 1. 分支模型

| 分支 | 用途 | protection |
|------|------|-----------|
| `main` | 生产分支 | 锁（必填 CI + 必走 PR + 禁 force push / 删除） |
| `staging` | 灰度 / pre-prod | 同 main |
| `feature/*` / `fix/*` / `chore/*` / `test/*` / `docs/*` | 任务分支 | 无 |

任务分支建议短命名 + kebab-case：`feature/risk-engine-v1` / `fix/paper-trade-double-fill` / `chore/dep-bump-mastra` / `test/orchestrator-smoke`。

## 2. 何时走 staging

**必经 staging**（高风险）：

- 新策略（`services/research/` / `services/paper/` / `packages/orchestration/` 大改）
- 新 connector / 新 venue / 新 broker
- orchestrator schema 或 agent prompt 大改
- 改 `services/_shared/` 基础设施
- 改 D-9 风控 / live runner 链路

**直接 PR → main**（低风险）：

- bugfix / 文档 / 测试改动
- 一次性 refactor / 依赖升级
- 单文件 / 单模块局部改动
- workflow / CI 调整

判断不清就走 staging——多一道闸不算贵。

## 3. PR 流程

### 高风险路径

```
feature/* → PR → staging → Cloudflare preview deploy → 人工把玩 → PR staging → main
```

### 普通路径

```
feature/* → PR → main
```

每个 PR 自动触发：

1. **CI**（6 个必填 status check，详 `.github/workflows/ci.yml`）
2. **Claude PR Review**（按 CLAUDE.md red flag 自审，详 `.github/workflows/claude-review.yml`）
3. **Cloudflare Pages preview deploy**（每 PR 独立 URL，评论里贴出）

`@claude` 在 PR / issue / review comment 里触发对话（详 `.github/workflows/claude.yml`）。

## 4. Branch protection 当前规则

main 与 staging 配置一致：

- **必填 6 个 CI status check**：`orchestration · typecheck + test` / `web · typecheck + build` / `跨文件一致性检验` / `python services · ruff + mypy (data|paper|research)`
- `required_pull_request_reviews.required_approving_review_count: 0`：必走 PR，但**不**强制 approval（单人项目，避免自批死锁）
- `allow_force_pushes: false`、`allow_deletions: false`
- `enforce_admins: false`：admin 紧急可绕过
- Claude PR Review **不**必填（LLM 偶尔抽风，避免卡 PR）

改 protection：

```bash
gh api -X PUT /repos/mirror29/inalpha/branches/<branch>/protection \
  -H "Accept: application/vnd.github+json" \
  --input <protection.json>
```

CI workflow 改 job 名时同步更新此处的 `contexts`，否则 PR 会一直等永远不跑的 check。

## 5. Commit 规范

- 中文 + `<type>(<scope>): <desc>` 格式
- type：`feat` / `fix` / `chore` / `docs` / `test` / `refactor` / `perf` / `ci`
- 可标 Phase D-N（例：`feat(paper): closed_trades pipeline (D-9 Slice 6)`）
- commit 前 `git status` 检查 untracked，**避免漏 add 让 CI 挂**（详 CLAUDE.md §4）

## 6. Code Review 鉴权

- Claude PR Review 走 Max 订阅 OAuth（GitHub Secret: `CLAUDE_CODE_OAUTH_TOKEN`），有效期 1 年
- 重生成：本地 `claude setup-token` → 浏览器授权 → `gh secret set CLAUDE_CODE_OAUTH_TOKEN`
- 出 401 直接换新 token；token 暴露过（聊天 / 截图 / 日志）也立即 revoke + 重生成

## 7. 紧急 hotfix（只在生产 P0 故障）

1. admin 直推 main（`enforce_admins: false` 允许，但**不要养成习惯**）
2. push 完立即 backfill PR + 写 incident note（postmortem）
3. cherry-pick 或 merge 到 staging 防分支漂移

## 8. 字符上限提醒

CLAUDE.md 4000 字节硬上限——本文件承载所有 git workflow 细节，CLAUDE.md §3 那行 git 协作只保留一行指针。**所有协作流程的新增 / 修改都写本文件，不要往 CLAUDE.md 塞**。

---

更早的协作约定见 CLAUDE.md §3（命名 / 不要碰 / commit）+ §4（CI 红线）+ §3.1 / §3.2（金融时效性 + prompt 工程纪律）。
