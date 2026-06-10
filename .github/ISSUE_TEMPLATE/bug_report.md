---
name: Bug report / 问题报告
about: Report a bug or unexpected behavior / 报告一个 bug 或异常行为
title: "[bug] "
labels: bug
---

> Inalpha is in **alpha** (Phase D-11 landed). **Please confirm the problem is "implemented incorrectly," not "not yet implemented."**
> See `CLAUDE.md §3` and `docs/04-current-state.md` for the roadmap.
>
> 项目处于 alpha 阶段（Phase D-11 已落地）。**请先确认你的问题不是「未实现」而是「实现错了」。**
> Roadmap 见 `CLAUDE.md §3` 与 `docs/04-current-state.md`。

## Environment / 环境

- Affected service / 受影响 service: `services/data` / `services/paper` / `services/research` / `packages/orchestration` / other
- Current Phase / 当前 Phase: D-11 / D-12+ / E-series / N/A
- OS / Node / Python versions:
- Git commit SHA (`git rev-parse --short HEAD`):

## Expected behavior / 期望行为

What should have happened.

简述「应该发生什么」。

## Actual behavior / 实际行为

What actually happened. For crashes, please paste the full stack trace.

简述「实际发生了什么」。如果是 crash 请贴完整 stack trace。

## Minimal reproduction / 最小复现命令

Please paste a command sequence that reproduces the problem from a clean clone — the shorter the better:

请贴一段能从干净 clone 复现问题的命令序列，越短越好：

```bash
# Example / 例如
pnpm i && uv sync
cd services/paper && uv run python -m inalpha_paper.main &
# ...the request that triggers the bug
```

## Consistency check / 一致性检查

- [ ] Ran `bash scripts/check-consistency.sh`. Result / 结果: (pass / which checks failed)

## Other / 其他

Logs, screenshots, related issues, etc.

日志、截图、相关 issue 链接等。
