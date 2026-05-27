<div align="center">

<img src="assets/mascot-avatar.png" alt="Inalpha" width="200" />

<h1>Inalpha</h1>

<p><strong>可审计的量化 agent，能进化的策略。</strong></p>

<p>因子实验室 &nbsp;·&nbsp; 风控引擎 &nbsp;·&nbsp; 策略进化 &nbsp;·&nbsp; Plan/Exec 审批</p>

<p>
  <a href="README.md">English</a> &nbsp;|&nbsp; <strong>中文</strong>
</p>

<p>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-AGPL--3.0-C8463C.svg" alt="License" /></a>
  <img src="https://img.shields.io/badge/status-alpha%20·%20Phase%20D--9-9E7B4B.svg" alt="Phase" />
  <img src="https://img.shields.io/badge/built%20with-Mastra%20%2B%20FastAPI-D4A744.svg" alt="Built with" />
  <img src="https://img.shields.io/badge/python-3.12+-1A1714.svg" alt="Python" />
  <img src="https://img.shields.io/badge/typescript-5.x-1A1714.svg" alt="TypeScript" />
</p>

<p><em>每个因子提案、每次策略变异、每笔订单路由——都有日志、有版本、可复核。LLM 只负责写代码，工程纪律为每个决策背书。</em></p>

</div>

---

## Overview

Inalpha 是一个**用工程纪律驱动的专业量化 agent 框架**。它不把 LLM 当作黑箱信号源，而把它视作受 hooks / permissions / plan-exec / 一次性签名约束的代码协作者——每一步关键动作都留痕、可版本化、可复核。

在这套护栏之上，铺开四条能力线：

- **因子实验室。** Agent 负责 formalize、compute、IC 检验、多重检验校正、register；每个假设都带作者、时间戳与经济故事门的判定记录。
- **风控引擎。** 仓位上限、价格偏离、回撤一票否决等规则在 HTTP 边界声明式生效，不写在 prompt 里。
- **策略进化。** LLM 变异完整 Python 源码，三道沙盒（AST 审计 / 子进程隔离 / `Strategy` 协议契约）先于任何候选回测；多目标 fitness（Sharpe + Calmar − turnover − drawdown 一票否决），单一指标卷不了。
- **Plan/Exec 审计链。** `trade.create_plan → approve → execute_plan` 配一次性、短 TTL 的 `approval_token`；LLM **没有**直接下单路径。

项目命名取自日本稻荷狐神 **Ina**ri 与量化术语 **alpha**。

> **当前状态：** Inalpha 处于 **alpha** 阶段（Phase D-9，LLM 自创策略 + 风控引擎规则化 + 多市场路由）。欢迎读代码、参与设计——**暂不建议用真实资金跑。**

---

## Design Principles

| 信条 | 内涵 |
|---|---|
| **纪律优先于氛围** | hooks、permissions、plan-exec、一次性 approval_token 都在 config 声明，不在 prompt 里——护栏失效有单点可调试 |
| **Agent 是 first-class** | 研究、决策、风控、复盘各司其职，立场对抗、tool 各异、决策必留痕；不是包装层 |
| **透明胜于精确** | 宁可要一个"我不知道"的 agent，也不要一个"看起来笃定但说不清依据"的 agent |
| **统一内核** | 回测、模拟盘、实盘共用一份策略代码——切换的是 Clock 与 Gateway，不是逻辑。行为分歧时根因只剩物理差异（滑点、延迟、数据精度），而非"两条代码路径" |
| **长期主义复利** | 基础设施扎实优先于花活；项目跑得久比跑得快重要 |

---

## 系统架构

<p align="center">
  <img src="assets/agent-runtime.svg" alt="Inalpha 系统架构" width="720" />
</p>

四层，自顶向下：

- **L1 · 用户入口。** 当前通过 `mastra dev` playground 或直接调 tool CLI 跟系统交互。专门的 Web UI 推迟到 Phase E+。
- **L2 · `packages/orchestration`（Mastra · TypeScript）。** agents、tools、hook/permission middleware、in-memory plan store、对话 memory、telemetry 6 个子模块并列。**LLM 只在这一层跑**。
- **L3 · Python kernel services（FastAPI）。** 每个 service 是独立的有状态进程。已落地：`services/data`（行情接入）、`services/paper`（事件驱动内核，回测 = 模拟盘 = 实盘同代码）、`services/research`（多 agent 起手脚手架；完整 bull/bear 辩论闭环放在 Phase E+）。`Strategy Evolution` 异步循环并行运行。
- **L4 · 持久化 + 外部依赖。** Postgres + TimescaleDB 承载全部时序与业务状态。外部行情覆盖 crypto + 美股 + A 股 + 港股 + 日韩澳印巴英德等主要单股市场 + 全球指数 + FRED 宏观——orchestrator 按"市场分类"自动路由 venue。

### Strategy Evolution 异步循环（Phase E+）

<p align="center">
  <img src="assets/strategy-evolution.svg" alt="策略进化循环" width="720" />
</p>

进化循环与 agent runtime 并行异步运行，胜出策略推回 `services/paper` 跑回测评估。沙盒门、fitness 函数、E1 → E4 渐进路径等细节见下方[核心能力 §3](#3-策略进化--沙盒下的自我演化)。

两张图由 D2 源文件渲染——[`assets/agent-runtime.d2`](assets/agent-runtime.d2) / [`assets/strategy-evolution.d2`](assets/strategy-evolution.d2)。当前已落地的模块清单、未完成项、决策链路 sequence diagram 见 [`docs/04-current-state.md`](docs/04-current-state.md)。

---

## 核心能力

下面四条能力的产出，从落地的第一天就是可审计的——不是事后加补丁。

### 1. 因子实验室 · 把每一个 alpha 想法记成档

「因子假设」就是一句关于"什么能预测收益"的猜想——比如"低波动股长期跑赢"、"期权 skew 在大跌前会变陡"。传统做法是研究员一个个验，一天能跑完 5-10 个就不错了。Inalpha 让 agent 替你跑，但不允许它走捷径。

- **聊一聊就开工。** 用自然语言抛一个假设，agent 自动把它形式化、计算因子值、跑标准统计检验。
- **「为什么」是必经的门槛。** 没有 economic story 的因子，进不了因子库——这是规则，不是建议。
- **经典坑全是 hook 拦着的。** 前视偏差、生存偏差、过参数搜索、样本不足、归一化泄漏，五道中间件检查在结果污染之前各自拦下一种。
- **不能自动上线。** 因子注册到正式库这一步永远只能人工执行；被拒的因子也存档备复盘，不会悄无声息地丢掉。

> 对话式工具在 L0；固定验证流程在 L1；多 agent 研究小组在 L2；每周自动扫描在 L3。设计细节见 `docs/03-kernel-design.md`。

### 2. 风控与审计 · LLM 不可绕过审批触达订单

让 LLM 直接调 `submit_order` 是亏钱最快的姿势。Prompt 里写"不要超过 10% 仓位"不是约束，只是建议——足够自信的模型分分钟会覆盖。所以 Inalpha 把风控从 prompt 挪进了中间件。

- **下单分三步。** 任何交易意图都走 *提议 → 审批 → 执行*。审批（由风控 agent、人工，或自动规则）签发一次性、短时效的签名 token；执行消耗 token，token 一用即作废。
- **硬规则落在服务边界。** 仓位上限、价差守门、回撤一票否决、品种类别上限——在任何状态变更之前就生效。违规订单被直接拒掉，拒因关联到原始提议留档。
- **完整审计链。** 每一次提议、审批、执行都持久化记录——谁、为什么、何时、token 的完整生命周期。同一份记录用于事后复盘，也回灌给策略进化作为反馈。

### 3. 策略进化 · 让策略写出更好的下一代

人工写策略有速度上限；传统调参只能转旋钮，发现不了"在 SMA 交叉里加一个 RSI 过滤"这种结构性创新。Inalpha 让 LLM 直接改 Python 源码，但每个候选都必须先过沙盒，才能跑回测。

- **改的是小 diff，不是大重写。** LLM 拿到现有源码 + 上一次回测报告，返回一段短小的 unified diff——好审，好回滚。
- **回测之前三道沙盒。** 静态代码审查、子进程隔离运行、`Strategy` 接口契约校验——恶意或残缺代码根本到不了回测引擎。
- **保留多样性、不卷单一指标。** 候选按"收益 + 风险调整后收益 − 换手率 − 回撤一票否决"综合打分，并按行为分布存进一个 grid，避免种群塌缩到单一 Sharpe 极致解。
- **端到端可复现。** 每个候选的父策略、prompt、沙盒判定、得分都有版本——整条进化链可重放。

> D-9 上线 E1（单代闭环），目标渐进到 E4（进化循环作为单个 MCP tool 暴露给 orchestrator）；每一级要稳定运行 2 周才升下一级。

### 4. Swarm · 一次跑几十个回测

真实量化研究天生是并发的：5 标的 × 3 因子族 × 4 时段 = 60 个回测。在 agent runtime 里一个一个串行跑是死路。

Inalpha 把*调度*和*算力*分开：agent runtime 负责扇出网格、聚合结果；真正的 Python worker pool 在 `services/paper` 里跑，多进程 + 资源限制。"用 momentum / mean-reversion / breakout 在 BTC ETH SOL BNB AVAX 上跑 2024 回测" 就是一次 workflow 调用，返回一条 Pareto 前沿。

> 当前实现（S1）：单机进程池、并发 4、单次最多 20 个回测组。

---

## Built on the shoulders of

Inalpha 不是从零发明——它有选择地继承前人的最优解，并明确**借鉴边界**：

| 项目 | 我们继承的设计 | 我们没有继承的部分 |
|---|---|---|
| [**Nautilus Trader**](https://github.com/nautechsystems/nautilus_trader) | `backtest = paper = live` 同代码不变量；事件驱动内核；统一的 Clock / MessageBus 抽象 | Rust 实现（MVP 选择 Python 优先生态厚度，未来评估关键路径下沉 Rust） |
| [**vnpy**](https://github.com/vnpy/vnpy) | Gateway 抽象层与多市场接入哲学 | CTP / XTP 这类国内券商专用通道（我们走 CCXT + 直连 REST） |
| [**Microsoft qlib**](https://github.com/microsoft/qlib) | 因子表达 DSL、Alpha158 范式、point-in-time universe 设计 | 端到端的 ML 训练 pipeline（qlib 作为 factor-lab 而非替代） |
| [**TradingAgents**](https://github.com/TauricResearch/TradingAgents) | Multi-agent 立场对抗（bull / bear / risk）作为**研究**辩论——slotted 进 `services/research`（Phase E+） | 把这套模式放到执行路径（我们把执行交给状态机 + permissions） |
| [**Anthropic Claude Code**](https://claude.com/claude-code) | Hooks（PreToolUse / PostToolUse / Stop）、声明式 permissions、Plan/Exec 分离、MCP 协议、subagent 隔离、prompt cache 工程化 | Bash / file 这类 coding 域特有的 tool（交易场景重新设计 tool 集） |
| [**Mastra**](https://mastra.ai) | TypeScript agent 编排骨架、`createTool` / `createWorkflow` 原语 | — |

---

## For whom

| 适用 | 价值 |
|---|---|
| 量化研究员与 quant 方向学生 | LLM agent 加速研究，统一回测/实盘技术栈 |
| 交易系统工程师 | 现代 agent 与传统内核的整合样板，对照 Nautilus / qlib / vnpy 的工程权衡 |
| AI agent 开发者 | 真实金融场景中 multi-agent + hooks + permissions 的工程落地 |
| 个人交易者（研究取向） | 一个可对话的研究助手 + 一个能沉淀策略的工程化框架 |

| 不适用 | 建议方向 |
|---|---|
| 寻找"AI 信号"订阅或跟单服务 | Inalpha 是工具不是产品 |
| 毫秒级高频交易 | [Nautilus Trader](https://github.com/nautechsystems/nautilus_trader)（Rust 内核） |
| 做市与跨所套利专项 | [Hummingbot](https://github.com/hummingbot/hummingbot) |
| 即插即用的生产级量化系统 | Nautilus Trader（已成熟） |

---

## Quick Start

```bash
pnpm i                          # Node 包（packages/orchestration）
uv sync                         # Python 包（services/_shared, data, paper）

bash scripts/dev.sh             # 一键起 data(8001) + paper(8002) + mastra(4111)
bash scripts/dev.sh logs        # 跟随日志
bash scripts/dev.sh stop        # 停止全部
```

随后打开 `http://127.0.0.1:4111` 的 `mastra dev` playground，与 orchestrator agent 对话。

想用 3 个独立 terminal 手动起？见 [`AGENTS.md §4`](AGENTS.md)。

---

## AI Collaboration

Inalpha **工具中立、本地优先**。策略、数据、决策记录全部留在你的仓库里；LLM 调用走外部 provider，但**结构化输出与 cache 控制属于仓库本身**——可观测、可审计、可换 provider。硬约束（命名、不可碰目录、commit 规范、tool description 三段式）只声明一次，由所有 AI 编程工具共同读取：

- [`CLAUDE.md`](CLAUDE.md) — Claude Code 项目级 memory
- [`AGENTS.md`](AGENTS.md) — Cursor / OpenAI Codex / Aider / Continue / Cline 等通用入口
- `scripts/check-consistency.sh` — 跨文件一致性的机械化检验

---

## Acknowledgments

Inalpha 是站在别人好点子上做出来的。我们把借鉴的东西明确点名，是想说清楚：Inalpha 不是从零发明的。

**交易系统设计**
- [Nautilus Trader](https://github.com/nautechsystems/nautilus_trader) —— 回测 / 模拟 / 实盘的同代码不变量，以及事件驱动内核
- [vnpy](https://github.com/vnpy/vnpy) —— Gateway 抽象与多市场接入思路
- [Microsoft qlib](https://github.com/microsoft/qlib) —— 因子表达 DSL 与 point-in-time universe 处理
- [Hummingbot](https://github.com/hummingbot/hummingbot) · [Freqtrade](https://github.com/freqtrade/freqtrade) —— 开源 crypto 工具能达到的高度

**Agent 与 LLM 工程化**
- [TradingAgents](https://github.com/TauricResearch/TradingAgents) —— 把多 agent 立场对抗辩论带入金融
- [Anthropic](https://anthropic.com) 与 [Claude Code](https://claude.com/claude-code) 团队 —— 把 hooks / permissions / plan-exec / MCP / subagent 做成可借鉴的工程原语
- [Mastra](https://mastra.ai) —— TypeScript agent 编排骨架
- [Model Context Protocol](https://modelcontextprotocol.io) —— 让工具免胶水接入的开放协议

**基础设施**
- [PostgreSQL](https://postgresql.org) · [TimescaleDB](https://timescale.com) · [FastAPI](https://fastapi.tiangolo.com) · [CCXT](https://github.com/ccxt/ccxt) · [Next.js](https://nextjs.org) · [CopilotKit](https://copilotkit.ai) · [uv](https://github.com/astral-sh/uv) · [pnpm](https://pnpm.io)

也献给所有不接受"黑盒 AI 信号"的量化研究者——这个项目是写给你们的。希望 Inalpha 能在合适的时机回馈大家。

---

## License

**[GNU AGPL-3.0](LICENSE)** — 自由软件，带强网络 copyleft。

- 允许：任何用途（个人研究、学术、商业内部使用、与 AGPL 兼容的开源项目集成）
- 要求：如果你修改 Inalpha 并以网络服务形式提供给他人，必须按 AGPL-3.0 公开完整对应源码
- 商业授权（闭源 / 不愿公开源码的托管 SaaS）：请提 issue 单独洽谈双重许可

---

<div align="center">
  <sub>
    <strong>Inalpha</strong> &nbsp;·&nbsp; Where Inari meets alpha &nbsp;·&nbsp; 2026
  </sub>
</div>
