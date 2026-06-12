# Attribution

- **Upstream**: [simonlin1212/a-stock-data](https://github.com/simonlin1212/a-stock-data)（Apache-2.0，LICENSE 原文见同目录）
- **Vendored at**: v3.2.2（2026-06-03 release；2026-06-12 改写）
- **方法论源头**: 该项目 SKILL.md 内嵌的 A股调研工作流（单票估值 / 批量对比 /
  主题研报 / 新标的调研）与数据交叉验证纪律

## 改写说明（非逐字搬运）

按 Inalpha 工程纪律全文重写，主要差异：

1. **内嵌 Python 数据配方全部剔除**：上游是"方法论 + 27 个数据端点配方"的自包含
   skill；Inalpha 把数据层落到 services/data 的 `/market/*` 端点与 `data.get_market_*`
   工具（直连配方另行移植，见 `services/data/src/inalpha_data/connectors/cn_market.py`），
   skill 只保留方法论——零数据零代码（ADR-0046 v1 不做 scripts 执行）
2. **数据步骤映射**：所有"查"步骤显式落到 Inalpha 工具（data.* / web.* /
   research.* / sandbox.*）；上游有而 Inalpha 暂无的数据维度（一致预期 EPS / 研报 /
   龙虎榜 / 解禁 / 两融 / 股东户数）改为"web 检索 + 找不到显式声明缺口"硬规则，
   并加"禁止训练记忆代答 + 数据时点标注"（金融时效性纪律 §3.1）
3. **frontmatter description 意图化**：触发条件按意图模式描述（CLAUDE.md §3.2），
   不写死触发短语
4. **去具体标的**：上游输出示例含真实 ticker（688017 等），全部改为占位符模板，
   避免锁死用户预期
5. **30x PE 锚点与各阈值标注为"经验值"**：上游当定理用，这里要求输出时如实
   标注其经验性质
6. **主题研报流程未保留**：依赖 iwencai / 东财 reportapi（Inalpha 无对应工具），
   归入"数据缺口"处理；盘面归因互证规则与 orchestrator 六维归因框架衔接（D-12+）
