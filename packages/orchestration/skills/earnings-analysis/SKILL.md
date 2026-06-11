---
name: earnings-analysis
description: 财报季复盘方法论（机构级 earnings update 工作流的对话版）。当用户想分析某家公司刚发布的季报 / 半年报 / 年报——beat/miss 拆解、分部与毛利变化、指引解读、预期修正、对投资论点的影响时使用。输出结构化复盘分析，不是泛泛的"业绩不错"。仅适用于已发布的财报复盘；财报前瞻预演、首次覆盖全景研究、单纯查最新财务指标不用本 skill。
license: Apache-2.0
metadata:
  upstream: https://github.com/anthropics/financial-services
  upstream_path: plugins/vertical-plugins/equity-research/skills/earnings-analysis
  upstream_commit: 4bbabc7cd1a474c1667fa05a2bfe58e411dcf9c1
  adapted_by: Inalpha (ADR-0046)
---

<!-- Adapted from anthropics/financial-services (Apache-2.0). 改写说明见 ATTRIBUTION.md -->

# 财报复盘（Earnings Update）

机构研究的财报更新范式：**只讲"新东西"**——beat/miss 多少、为什么、指引怎么变、预期怎么改、论点是否成立。不重述公司背景，不写百科。

## 数据纪律（最高优先级）

**训练记忆里的"最新财报"几乎必然过时。**开始前四步强制：

1. 确认 as_of（runtime_facts 里的真实今天）
2. `web.search_news` / `web.search` 搜该公司**最新**财报发布——禁止直接用记忆里的季度数据
3. 核对发布日期距 as_of 是否在 3 个月内；不在就换关键词再搜，或明确告诉用户"目标季度尚未发布/已过时"
4. 关键来源（财报原文 / 业绩说明会记录 / 公司公告页）用 `web.fetch` 读正文，记录 published_at

| 步骤 | 工具 |
|---|---|
| 找最新财报发布 / 业绩会 / 共识预期报道 | `web.search_news`、`web.search` |
| 读财报原文 / transcript / 公告正文 | `web.fetch` |
| 公司名 → 代码 | `data.search_symbol` |
| 核验财务指标（毛利 / 现金流 / 周转） | `data.get_fundamentals` |
| 财报后股价反应 | `data.get_bars`（默认 fresh）、`data.get_ticker` |
| 技术面交叉验证 | `factor.timing` |

每个数字必须可溯源（来源 + 日期 + 链接，chat 里直接给链接）；共识预期拿不到时**显式说明**，改用"公司自身指引 + 历史趋势"做对比基准，不要编共识。

## 市场适配

不预设市场。披露文件按该市场体系找（美股 10-Q/8-K、A股季报+业绩说明会、港股中期报告……可 `skill.read("serenity", "references/market-source-playbook.md")` 取分市场来源路径）。回复语言跟随用户。

## 分析工作流

1. **Beat/Miss 拆解**（结论先行）：每个关键指标 vs 共识/指引差多少（绝对值+百分比）；超预期是**一次性还是可持续**；不及预期是公司问题还是行业问题
2. **分部 / 地区 / 渠道**：谁超谁拖，趋势 vs 前几季，管理层对各块的展望
3. **毛利与利润率**：方向 + 驱动（价格 / 结构 / 成本 / 经营杠杆）
4. **指引解读**：新 vs 旧指引、vs 市场预期；公司历史上习惯压低还是激进（指引可信度）；没给指引要明说，并基于结果给独立展望
5. **预期修正**：本年 + 明年关键指标的"旧 → 新 + 变化原因"对照表——这是本 skill 的核心交付，不能省
6. **论点影响**：结果强化还是削弱原投资逻辑；若用户在用 thesis-tracker 跟踪该标的，把本次结果作为 data point 更新论点记分卡
7. **股价反应校验**：财报后股价怎么走的（`data.get_bars`），市场解读与你的分析一致吗？不一致说明谁可能错了

## 输出形态

对话内结构化 markdown（**不是**长篇报告），通常含：

- **一句话标题**：带结论与方向（"X Q3：DTC 强劲抵消批发疲软，上调全年预期"），忌"X 公司季报分析"这种无观点标题
- beat/miss 摘要表（1-2 张紧凑表格，非全量三表）
- 预期修正对照表（旧 / 新 / 变化 / 原因）
- 论点影响 + 接下来要盯的催化与风险
- 来源清单（每条带日期与链接）+ **数据截止标注**

写法纪律见 `references/best-practices.md`（好/坏标题示例、十条要诀、常见错误清单）。

## 边界

研究复盘，不是投资建议：给"估值参考与论点判断"，不给买卖指令与目标价承诺。用户要据此调仓 → 走 `trade.create_plan` 审批链路。
