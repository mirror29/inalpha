<!--
感谢贡献！提交 PR 前请确认以下事项。
本项目 LICENSE 是 PolyForm Noncommercial 1.0.0，你的贡献也将以同样许可发布。
-->

## 这个 PR 做了什么

简述变更内容（1–3 句）。如果是 bug fix，链接对应 issue：`Fixes #...`

## 涉及范围

- 受影响 service：`services/data` / `services/paper` / `packages/orchestration` / 其他
- 当前 Phase：D-8a / D-8b / ...
- 类型：`feat` / `fix` / `refactor` / `docs` / `test` / `chore`

## 自检清单

- [ ] Commit message 遵循 `<type>(<scope>): <desc>` 中文格式（见 `CLAUDE.md §4`）
- [ ] 已跑 `bash scripts/check-consistency.sh`，全部 pass
- [ ] 如果改了 tool description，遵循三段式：「功能 + 何时用 + 何时不用 + 坑」
- [ ] 如果改了硬约束相关代码（permissions / hooks / LLM 下单路径），已在 PR 描述中说明
- [ ] 如果引入新依赖，已确认许可证与 PolyForm Noncommercial 兼容
- [ ] 没有动 `services/_shared/`、`.mastra/`、`docs/miro/`

## 测试

简述你怎么验证这个改动是对的。可以是手动复现步骤、新加的测试用例、或 smoke test 输出。

## 截图 / 录屏（如适用）

UI 或 agent 行为变化请贴对比。
