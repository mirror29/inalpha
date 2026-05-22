---
name: Feature request / 功能建议
about: 提议一个新功能或改进方向
title: "[feature] "
labels: enhancement
---

> 在提议之前请先确认：本项目**不是**开箱即用的策略平台，也**不是** LangChain / AutoGen 包装。
> 边界见 `docs/00-context.md`。仅接受与「AI agent 编排 × 量化研究」交叉点相关的功能。

## 想解决的问题

具体描述你在用 Inalpha 时遇到的实际限制，**不要**直接描述「想要什么功能」——先讲场景。

## 提议的方向

你认为可以怎么解决？（如果暂时没想法，留空也可以）

## 这个建议属于哪个 Phase？

- [ ] D-8b（持久化 trade_plans / approval_tokens）
- [ ] D-9（RiskEngine 规则化 + paper 真接入）
- [ ] D-10 及之后
- [ ] 不确定 / 需要讨论

## 是否破坏现有约束？

请确认这个建议**不**违反以下硬约束（见 `AGENTS.md §3 / §8`）：

- [ ] 只涉及 crypto 市场，不引入 A 股 / 美股逻辑
- [ ] 不让 LLM 获得直接下单路径
- [ ] 不动 `services/_shared/`
- [ ] 不引入与 AGPL-3.0 不兼容的依赖（注意 LICENSE 是 AGPL-3.0）

## 其他

参考实现、相关论文、其他项目的做法等。
