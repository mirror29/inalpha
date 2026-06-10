---
name: Feature request / 功能建议
about: Propose a new feature or improvement direction / 提议一个新功能或改进方向
title: "[feature] "
labels: enhancement
---

> Before proposing, please confirm: Inalpha is **not** a plug-and-play strategy platform, **nor** a LangChain / AutoGen wrapper.
> Boundaries live in `docs/00-context.md`. We only accept features at the intersection of **AI agent orchestration × quant research**.
>
> 在提议之前请先确认：本项目**不是**开箱即用的策略平台，也**不是** LangChain / AutoGen 包装。
> 边界见 `docs/00-context.md`。仅接受与「AI agent 编排 × 量化研究」交叉点相关的功能。

## Problem to solve / 想解决的问题

Concretely describe the real limitation you ran into while using Inalpha. **Do not** describe "the feature you want" — describe the scenario first.

具体描述你在用 Inalpha 时遇到的实际限制，**不要**直接描述「想要什么功能」——先讲场景。

## Proposed direction / 提议的方向

How do you think it could be solved? (Leave blank if you don't have an idea yet.)

你认为可以怎么解决？（如果暂时没想法，留空也可以）

## Which Phase does this belong to? / 这个建议属于哪个 Phase？

- [ ] D-series (data / paper-trading mainline — D-11 landed: multi-market paper, cross-currency cash, live runner)
- [ ] research-hub (#6 — research workspace / 研究工作台)
- [ ] E-series (strategy evolution E2+ (#7), factor L2–L3, research crew)
- [ ] Unsure / needs discussion / 不确定 / 需要讨论

## Does it violate any hard constraints? / 是否破坏现有约束？

Please confirm this proposal does **not** violate the hard constraints (see `AGENTS.md §3 / §8`):

请确认这个建议**不**违反以下硬约束（见 `AGENTS.md §3 / §8`）：

- [ ] Does not give the LLM a direct order-placement path / 不让 LLM 获得直接下单路径
- [ ] Does not modify `services/_shared/` / 不动 `services/_shared/`
- [ ] Does not introduce a dependency incompatible with AGPL-3.0 / 不引入与 AGPL-3.0 不兼容的依赖
- [ ] Does not hardcode a specific language, market, or instrument (Inalpha is built for global users — see `CLAUDE.md §3`) / 不在 prompt 或路由中硬编码语言 / 市场 / 品种

## Other / 其他

Reference implementations, related papers, how other projects approach it, etc.

参考实现、相关论文、其他项目的做法等。
