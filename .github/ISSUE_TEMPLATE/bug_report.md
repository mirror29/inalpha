---
name: Bug report / 问题报告
about: 报告一个 bug 或异常行为
title: "[bug] "
labels: bug
---

> 项目处于 alpha 阶段（当前 Phase D-8a）。**请先确认你的问题不是「未实现」而是「实现错了」。**
> Roadmap 见 `CLAUDE.md §3` 与 `docs/04-current-state.md`。

## 环境

- 受影响 service：`services/data` / `services/paper` / `packages/orchestration` / 其他
- 当前 Phase：D-8a / D-8b / ...
- OS / Node / Python 版本：
- Git commit SHA（`git rev-parse --short HEAD`）：

## 期望行为

简述「应该发生什么」。

## 实际行为

简述「实际发生了什么」。如果是 crash 请贴完整 stack trace。

## 最小复现命令

请贴一段能从干净 clone 复现问题的命令序列，越短越好：

```bash
# 例如
pnpm i && uv sync
cd services/paper && uv run python -m inalpha_paper.main &
# ...触发问题的请求
```

## 一致性检查

- [ ] 已跑 `bash scripts/check-consistency.sh`，结果：（pass / 哪几项 fail）

## 其他

日志、截图、相关 issue 链接等。
