# Attribution

- **Upstream**: [muxuuu/serenity-skill](https://github.com/muxuuu/serenity-skill)（MIT，LICENSE 原文见同目录）
- **Vendored at**: commit `c2fe93deedfd0d1bd9fe7ef0601ea1b9c20ea24a`（2026-06-11）
- **方法论源头**: 公开的 Serenity / @aleabitoreddit 风格供应链瓶颈研究法的社区整理版

## 改写说明（非逐字搬运）

按 Inalpha 工程纪律全文重写，主要差异：

1. **frontmatter description 意图化**：去掉上游写死的触发短语（"用 Serenity 的方式看"等），改为意图模式描述——全球用户用任何措辞表达"拆产业链找瓶颈"意图都应命中
2. **数据步骤映射**：所有"查热点 / 行情 / 财报"步骤显式落到 Inalpha 工具（web.* / data.* / factor.* / research.*），并加"禁止训练记忆代答 + 输出标注数据截止"硬规则（金融时效性纪律）
3. **市场无关化**：弱化上游的 A股默认语境，市场与回复语言均跟随用户
4. **不 vendor**：`scripts/`（信任边界，评分逻辑已转为 SKILL.md 内 rubric 表）、`examples/`（含具体标的，避免锁死预期）、`evals/`、人设与公开资料类 references
5. **references 7 取 4**：保留 deep-research-workflow / evidence-ladder / market-source-playbook / risk-and-compliance，均为改写压缩版
6. **合规衔接**：交易动作接到 Inalpha 的 trade.create_plan 审批链路
