<!--
Thanks for contributing! Please review the items below before submitting.
This project is licensed under GNU AGPL-3.0; your contribution ships under the same license.

感谢贡献！提交 PR 前请确认以下事项。
本项目 LICENSE 是 GNU AGPL-3.0，你的贡献也将以同样许可发布。
-->

## What this PR does / 这个 PR 做了什么

Briefly describe the change (1–3 sentences). For bug fixes, link the related issue: `Fixes #...`

简述变更内容（1–3 句）。如果是 bug fix，链接对应 issue：`Fixes #...`

## Scope / 涉及范围

- Affected service / 受影响 service: `services/data` / `services/paper` / `services/research` / `packages/orchestration` / other
- Current Phase / 当前 Phase: D-9 / D-10+ / E-series / N/A
- Type / 类型: `feat` / `fix` / `refactor` / `docs` / `test` / `chore`

## Self-review checklist / 自检清单

- [ ] **Commit message follows `<type>(<scope>): <desc>` in Chinese** (see `CLAUDE.md §3`). This project commits in Chinese even when contributors usually commit in English — please keep the convention.
- [ ] Ran `bash scripts/check-consistency.sh` locally; all checks pass.
- [ ] If you touched tool descriptions, they follow the three-part style: *function + when to use + when not to use + gotchas*.
- [ ] If you changed code related to hard constraints (permissions / hooks / LLM order paths), it's called out in the PR description.
- [ ] If you introduced a new dependency, its license is AGPL-3.0 compatible.
- [ ] You did not modify `services/_shared/`, `.mastra/`, or `docs/miro/` (shared infrastructure & private decision records — see `CLAUDE.md §3` for the rationale).

## Testing / 测试

Describe how you verified the change is correct — manual repro steps, new tests added, or smoke-test output.

简述你怎么验证这个改动是对的。可以是手动复现步骤、新加的测试用例、或 smoke test 输出。

## Screenshots / recordings (if applicable) / 截图 / 录屏（如适用）

For UI or agent-behavior changes, please attach before/after comparisons.

UI 或 agent 行为变化请贴对比。
