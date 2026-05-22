<div align="center">

<img src="assets/mascot-avatar.png" alt="Inalpha" width="200" />

<h1>Inalpha</h1>

<p><em>Find alpha with a fox's eye.</em></p>

<p>The quant familiar &nbsp;·&nbsp; backtest = paper = live</p>

<p>
  <a href="README.md">English</a> &nbsp;|&nbsp; <strong>中文</strong>
</p>

<p>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-PolyForm--NC%201.0.0-C8463C.svg" alt="License" /></a>
  <img src="https://img.shields.io/badge/built%20with-Mastra%20%2B%20FastAPI-D4A744.svg" alt="Built with" />
  <img src="https://img.shields.io/badge/python-3.12+-1A1714.svg" alt="Python" />
  <img src="https://img.shields.io/badge/typescript-5.x-1A1714.svg" alt="TypeScript" />
</p>

</div>

---

## Overview

Inalpha 是一个**面向严肃研究的开源量化交易框架**。它把多 agent 大语言模型协作、统一的交易内核、以及一套声明式工程护栏融合为同一个系统——回测、模拟盘与实盘共用一份策略代码，研究、决策、风控由立场对抗的 agent 协同完成，而不是把 LLM 当作黑箱信号源使用。

项目命名取自日本稻荷狐神 **Ina**ri 与量化术语 **alpha**，承袭"狐眼洞察、稳健致远"之意。

---

## Design Principles

| 信条 | 内涵 |
|---|---|
| **统一内核** | 同一份策略代码，跑回测、跑撮合、跑实盘——行为必须一致，否则一切失去意义 |
| **Agent 是 first-class** | 研究、决策、风控、复盘各司其职，立场对抗、tool 各异、决策必留痕；不是包装层 |
| **透明胜于精确** | 宁可要一个"我不知道"的 agent，也不要一个"看起来笃定但说不清依据"的 agent |
| **工程纪律胜于巧妙小聪明** | 决策记录、测试、声明式护栏先行；clever 代码是 bug 温床 |
| **长期主义复利** | 基础设施扎实优先于花活；项目跑得久比跑得快重要 |

---

## 系统架构

<p align="center">
  <img src="assets/agent-runtime.svg" alt="Inalpha 系统架构" width="720" />
</p>

四层，自顶向下：

- **L1 · 用户入口。** 当前通过 `mastra dev` playground 或直接调 tool CLI 跟系统交互。专门的 Web UI 推迟到 Phase E+。
- **L2 · `packages/orchestration`（Mastra · TypeScript）。** agents、tools、hook/permission middleware、in-memory plan store、对话 memory、telemetry 6 个子模块并列。**LLM 只在这一层跑**。
- **L3 · Python kernel services（FastAPI）。** 每个 service 是独立的有状态进程。已落地：`services/data`（行情接入）与 `services/paper`（事件驱动内核，回测 = 模拟盘 = 实盘同代码）。占位：`services/research`（多 agent 辩论）与 `Strategy Evolution` 循环。
- **L4 · 持久化 + 外部依赖。** Postgres + TimescaleDB 承载全部时序与业务状态。外部：**任何 CCXT 可达的交易所与市场**（当前 crypto；未来按项目演进可扩到期货、美股等），以及 LLM provider。

**LLM 没有直接下单路径**。所有下单意图必须走 `trade.create_plan → approve → execute_plan`，`approval_token` 一次性、默认 5 分钟过期。Trader agent 只产出 intent；Risk agent 默认拒绝、必要时派发 token；token 通过后才由 PostToolUse 转发到 `paper · POST /orders/submit`。这条规则落在 middleware 层——不是 agent 的 prompt 里——因而**可版本化、可单测、不可绕过**。

为保持线条整洁，主图有三条关系靠文字描述而非画在图里：agents 直接调用 **LLM Provider**（跨层）；orchestrator 后续接入 `services/research`（Phase E）与 `Strategy Evolution` 循环（MCP tool · E4+）走同一种方式；进化循环胜出的策略推回 `services/paper` 跑回测。另有两条 IO 通道未入图：**slash command**（绕过 LLM 的确定性入口）与只读 **Statusline**（实时持仓 / 待批 plans / 数据 staleness 等用户常看但 LLM 不需要的信息）。

### Strategy Evolution 异步循环（Phase E+）

<p align="center">
  <img src="assets/strategy-evolution.svg" alt="策略进化循环" width="720" />
</p>

一条独立异步循环，与单次 agent turn 解耦：LLM 以 diff 形式变异策略源码，候选代码必须通过三道沙盒门（AST 审查 / 子进程隔离 / `Strategy` protocol 契约），MAP-Elites 网格 × Island Model 维持种群多样性。胜出策略推回 `services/paper` 跑回测评估。自 **E4** 起以 MCP tool 形式暴露给 orchestrator，agent runtime 可主动触发并消费进化结果。

两张图由 D2 源文件渲染——[`assets/agent-runtime.d2`](assets/agent-runtime.d2) / [`assets/strategy-evolution.d2`](assets/strategy-evolution.d2)。当前已落地的模块清单、未完成项、决策链路 sequence diagram 见 [`docs/04-current-state.md`](docs/04-current-state.md)。

---

## 核心模块 · 找 alpha

项目叫 **Inalpha**，因为 **Ina**（稻荷狐神）+ **alpha**（超额收益）—— **"找 alpha" 是项目重心**。下面三个模块就是找 alpha 的核心引擎。

### 1. 因子发现 · 让 agent 真正挖出信号

**问题。** 传统因子研究的瓶颈是研究员的手工闭环：读论文 → 翻译成表达式 → 跑回测 → 查 lookahead bias → 查多重检验假阳性 → 再来。单人一天能验 5-10 个假设。

**设计。** 4 层渐进框架（L0 → L3），让 agent 替你做脏活——并自带护栏：

- **L0 · 对话式探索。** 8 个 `factor.*` tool（`formalize` / `compute` / `future_return_stats` / `ic_test` / `correlation_with_library` / `multiple_testing_check` / `propose` / `register`），用户在浏览器对话框抛假设，秒级算因子值与统计检验。
- **L1 · 假设→验证 workflow。** 强制 pipeline，含 `economic_story_gate`，不允许跳步。
- **L2 · 多 agent 研究小组。** HypothesisHunter / Formalizer / Coder / Backtester / Critic / Curator 分工挑战。
- **L3 · 自动 swarm + cron。** 每周扫 50-200 候选 → 通过的进 review queue。

**安全护栏。** 5 个 `PreToolUse` hook 确定性拦截因子挖矿的经典错误—— `lookahead-check` / `universe-survival-check` / `param-search-cap` / `min-sample-check` / `normalization-leak-check`。多重检验校正强制。`factor.register` 永远 `modelInvocable: false`—— agent 无法把因子自行推上生产。

### 2. 策略进化 · 策略自我演化

**问题。** 人工写策略有速度上限；传统 GA 只调参数，发现不了"在 SMA 交叉里加一个 RSI 过滤"这样的结构创新。

**设计。** FunSearch / AlphaEvolve 风格三件套——*LLM 作变异算子 + Island Model + MAP-Elites*——作用于**完整 Python 源码**，不是 AST 也不是参数向量：

- **变异。** LLM 收到当前策略源码 + 上一代回测报告，返回 unified diff。diff 短（cache 友好）、可审计、失败可回滚。
- **多样性。** MAP-Elites 二维 grid（年化收益 × 换手率）在每个行为格子里保留最优个体——种群永远不会塌缩到单一 Sharpe 极致解。
- **鲁棒性。** Island Model 跑 3-5 个独立 population 并行 + 周期迁移，防止早熟收敛。
- **安全。** 三道沙盒（AST 审计 / 子进程隔离 / `Strategy` 协议契约）在候选回测前必经。fitness 多目标合成（Sharpe + Calmar − turnover penalty − drawdown 一票否决），让 LLM 无法卷单一指标。

框架按 E1（单代闭环）→ E4（MCP tool 暴露给 Mastra）渐进，每层升级前需上一层稳定运行 2 周。

### 3. Swarm · 研究与回测的并发扩展

**问题。** 真实研究本身是并发的：5 标的 × 3 因子族 × 4 时段 = 60 个并行回测。在 Node 编排层串行跑是死路（单线程、CPU 重活）。

**设计。** **编排做什么 / 算力在哪**——边界清晰：

- Mastra workflow 只做 `expand → dispatch → await → aggregate`。Node 里不跑 CPU 重活。
- 真正的 worker pool 在 `services/paper` 的回测引擎内（Python multiprocessing + ulimit 隔离）。
- 同一个 swarm pattern 跑回测、跑模拟盘、跑实盘——切换是 Gateway 换，不是重写。
- `foreach({ concurrency: N })` 控制 in-flight job 数，引擎按自己节奏拉任务。

**带来什么。** "用 momentum / mean-reversion / breakout 在 BTC ETH SOL BNB AVAX 上跑 2024 回测" 变成一次 workflow 调用——扇出 15 个回测、聚合结果、把 Pareto 前沿摆给用户。同一组原语也驱动 paper account 批量评估和后续多策略实盘。

---

## Built on the shoulders of

Inalpha 不是从零发明——它有选择地继承前人的最优解，并明确**借鉴边界**：

| 项目 | 我们继承的设计 | 我们没有继承的部分 |
|---|---|---|
| [**Nautilus Trader**](https://github.com/nautechsystems/nautilus_trader) | `backtest = paper = live` 同代码不变量；事件驱动内核；统一的 Clock / MessageBus 抽象 | Rust 实现（MVP 选择 Python 优先生态厚度，未来评估关键路径下沉 Rust） |
| [**vnpy**](https://github.com/vnpy/vnpy) | Gateway 抽象层与多市场接入哲学 | CTP / XTP 这类国内通道（现阶段聚焦 crypto） |
| [**Microsoft qlib**](https://github.com/microsoft/qlib) | 因子表达 DSL、Alpha158 范式、point-in-time universe 设计 | 端到端的 ML 训练 pipeline（qlib 作为 factor-lab 而非替代） |
| [**TradingAgents**](https://github.com/TauricResearch/TradingAgents) | Multi-agent 立场对抗（bull / bear / risk）、辩论决策流程 | Demo 级 prompt 实现（我们把这套模式工程化为 hooks 与 plan-exec） |
| [**Anthropic Claude Code**](https://claude.com/claude-code) | Hooks（PreToolUse / PostToolUse / Stop）、声明式 permissions、Plan/Exec 分离、MCP 协议、subagent 隔离、prompt cache 工程化 | Bash / file 这类 coding 域特有的 tool（交易场景重新设计 tool 集） |
| [**Mastra**](https://mastra.ai) | TypeScript agent 编排骨架、`createTool` / `createWorkflow` 原语 | — |

---

## Design Advantages

Inalpha 的差异化不在"功能多"，而在"几件事一起做对了"：

### 1. 一套代码三种环境

策略类只写一次，三种 Gateway 切换跑——回测与实盘出现行为分歧时，根因不再"代码不一样"，而能聚焦到"撮合滑点 / 延迟 / 数据精度"等真正的物理差异。

### 2. Agent 之间的对抗与协作

Trader agent 想下单，Risk agent 默认拒绝，Research agent 给独立证据，Portfolio agent 看相关性——这是 TradingAgents 的范式，但**经过工程化**：所有 agent 间消息走 MessageBus、所有决策可回放、所有下单意图经 Plan/Exec 二阶段批准。

### 3. 声明式护栏，不是 vibe-coding

Hooks 在 `config/hooks.yaml` 声明、permissions 在 `permissions.yaml` 声明、MCP server 在 manifest 声明——**改护栏不动业务代码，护栏失效有单点可调试**。这是从 Claude Code 借来的"工程化 agent"核心思想。

### 4. AI 工具中立

[`CLAUDE.md`](CLAUDE.md) 让 Claude Code 用户获得项目级 memory；[`AGENTS.md`](AGENTS.md) 让 Cursor / Codex / Aider / Cline / Continue 用户共享同一套硬约束。换工具不丢规范。

### 5. 本地优先、开源优先

策略、数据、决策记录全部在本地。LLM 调用走外部 provider 但**结构化输出与 cache 控制在仓库内**，可观测、可审计、可换 provider。

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
pnpm i                                  # Node 包（packages/orchestration）
uv sync                                 # Python 包（services/_shared, data, paper）

# 起 services（分别开 terminal）
cd services/data  && uv run python -m inalpha_data.main
cd services/paper && uv run python -m inalpha_paper.main
cd packages/orchestration && pnpm dev   # mastra dev
```

打开 `mastra dev` 的 playground 即可与 orchestrator agent 对话。

---

## AI Collaboration

Inalpha 是一个**人机协作友好**的项目。无论使用何种 AI 编程工具，硬约束（命名、品牌名、不可碰目录、commit 规范、tool description 三段式）都统一声明在以下入口：

- [`CLAUDE.md`](CLAUDE.md) — Claude Code 项目级 memory
- [`AGENTS.md`](AGENTS.md) — Cursor / OpenAI Codex / Aider / Continue / Cline 等通用入口
- `scripts/check-consistency.sh` — 跨文件一致性的机械化检验

---

## Acknowledgments

Inalpha 站在巨人的肩膀上。向以下项目、作者与社区致以诚挚感谢：

**交易系统范式**

- [Nautilus Trader](https://github.com/nautechsystems/nautilus_trader) 与其核心维护者，让我们看清"同代码不变量"作为工程哲学的力量
- [vnpy](https://github.com/vnpy/vnpy) 社区，国内量化开源生态的拓荒者
- [Microsoft qlib](https://github.com/microsoft/qlib) 团队，把 quant 因子 pipeline 做成可教科书级别的开源样本
- [Hummingbot](https://github.com/hummingbot/hummingbot) 与 [Freqtrade](https://github.com/freqtrade/freqtrade) 项目，定义了 crypto 交易开源工具的可能性边界

**Agent 与 LLM 工程化**

- [TradingAgents](https://github.com/TauricResearch/TradingAgents) 与 Tauric Research，把 multi-agent 辩论决策范式带入金融领域
- [Anthropic](https://anthropic.com) 与 [Claude Code](https://claude.com/claude-code) 团队，把 hooks / permissions / plan-exec / MCP 等抽象做成可借鉴的工程原语
- [Mastra](https://mastra.ai) 团队，提供成熟的 TypeScript agent 编排骨架
- [Model Context Protocol](https://modelcontextprotocol.io) 开源规范及其贡献者

**基础设施**

- [PostgreSQL](https://postgresql.org) · [TimescaleDB](https://timescale.com) · [FastAPI](https://fastapi.tiangolo.com) · [CCXT](https://github.com/ccxt/ccxt) · [Next.js](https://nextjs.org) · [CopilotKit](https://copilotkit.ai) · [uv](https://github.com/astral-sh/uv) · [pnpm](https://pnpm.io) —— 让 Inalpha 得以站立的底层栈

**精神共鸣**

- 所有不接受"黑盒 AI 信号"的量化研究者——这个项目是写给你们的

愿 Inalpha 也能在合适的时机回馈社区。

---

## License

**[PolyForm Noncommercial 1.0.0](LICENSE)** — 开源但**禁止商业用途**。

- 允许：个人研究、学术、教育、非营利组织、开源项目集成
- 禁止：任何商业用途（含商业咨询、SaaS 化、商业内部使用）
- 商用授权：请提 issue 单独洽谈

---

<div align="center">
  <sub>
    <strong>Inalpha</strong> &nbsp;·&nbsp; Where Inari meets alpha &nbsp;·&nbsp; 2026
  </sub>
</div>
