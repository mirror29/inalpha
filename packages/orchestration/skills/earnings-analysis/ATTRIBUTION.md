# Attribution

- **Upstream**: [anthropics/financial-services](https://github.com/anthropics/financial-services)（Apache-2.0，LICENSE 原文见同目录）
- **Upstream path**: `plugins/vertical-plugins/equity-research/skills/earnings-analysis`
- **Vendored at**: commit `4bbabc7cd1a474c1667fa05a2bfe58e411dcf9c1`（2026-06-11）

## 改写说明（Apache-2.0 §4(b) 变更声明，非逐字搬运）

1. **交付形态**：原版产出 8-12 页 DOCX 报告（Times New Roman、8-12 张 matplotlib 图、可选 XLS 模型）；Inalpha 是对话 agent，改为结构化 markdown 对话交付，图表规范替换为紧凑表格，删除 DOCX/XLS/Python 依赖声明
2. **数据步骤映射**：原版的 web search/SEC EDGAR 流程映射到 Inalpha 工具（web.search_news / web.fetch / data.get_fundamentals / data.get_bars / data.search_symbol / factor.timing）；保留并强化原版的"训练数据过时"四步强制流程（与本仓库金融时效性纪律同源）
3. **市场无关化**：原版默认美股披露体系（10-Q/EDGAR/Bloomberg 共识），改为按用户市场选披露文件，共识不可得时显式降级为"指引 + 历史趋势"基准
4. **description 意图化**：去掉原版写死的触发短语（"Q1/Q2/Q3/Q4 results" 等），改为意图模式描述
5. **合规边界**：原版输出券商评级 + 目标价；改为"研究判断 + 估值参考"，不给买卖指令，交易动作接 trade.create_plan 审批链路
6. **references 3 取 1 改写**：report-structure.md（DOCX 页模板）不 vendor；workflow.md 的数据采集与分析框架已蒸馏进 SKILL.md；best-practices.md 改写为对话形态的写法要诀
