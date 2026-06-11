# Attribution

- **Upstream**: [anthropics/financial-services](https://github.com/anthropics/financial-services)（Apache-2.0，LICENSE 原文见同目录）
- **Upstream path**: `plugins/vertical-plugins/equity-research/skills/thesis-tracker`
- **Vendored at**: commit `4bbabc7cd1a474c1667fa05a2bfe58e411dcf9c1`（2026-06-11）

## 改写说明（Apache-2.0 §4(b) 变更声明，非逐字搬运）

1. **数据纪律注入**：原版假设用户口述即入档；改为新进展先经 web.search/web.fetch 核实、财务支柱经 data.get_fundamentals 取实时读数、催化剂只记"事件名+日期"禁展开结论（本仓库金融时效性纪律）
2. **持久化适配**：原版建议"store thesis data in a structured format across sessions"（Claude Code 文件系统）；Inalpha 对话 agent 无文件存储，改为对话内结构化 markdown 档案 + agent 记忆引用，并明示用户可自行保存
3. **description 意图化**：去掉写死触发短语（"update thesis for [company]" 等），改为意图模式
4. **合规边界**：明确记分卡建议非交易指令、调仓走 trade.create_plan；补充"模拟盘现货 long-only，看空论点的动作是回避而非开空"的本仓库约束
5. **与 earnings-analysis 联动**：财报类 data point 指向 earnings-analysis skill 做完整复盘后回填（原版无此衔接）
6. 输出形态去 Word doc，保留 markdown 记分卡
