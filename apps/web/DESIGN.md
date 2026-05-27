# Inalpha Web Design System

> 单源真相 (Single Source of Truth) for `apps/web`.
> 后续每个 PR 必须 grep 本文件自检漂移；任何与本文件冲突的实现以本文件为准（或先修本文件再改代码）。

**适用范围**：`apps/web`（官网 inalpha.dev）。不约束 Mastra playground、文档站点、未来的 CopilotKit chat UI（这些有独立约束）。

**预期读者**：
1. 在仓库内推进 redesign 的开发者
2. Claude Code / Cursor / 任何 AI agent（按 §11 prompt 喂入即可生成符合调性的代码）
3. open-design (nexu-io) 等外部 AI 设计生成工具（独立装在 `/Users/mirror/study/open-design`，本文件作为输入素材）

---

## 1. Tone & Philosophy

**一句话定调**：**Technical Broadsheet** —— engineering schematic × 1960s 技术期刊 × trading desk terminal。

| 是 | 不是 |
|---|---|
| 工程图纸（Apollo schematic、Bell Labs memo） | SaaS 模板的 marketing page |
| 1960s 技术期刊（精排版、italic display serif） | "AI 工具" 通用暗色 + cyan blob 模板 |
| Bloomberg terminal 的信息密度与 ticker | 鬼畜插画 + 巨字 wordmark |
| 招股说明书般的工程严谨 | 渐变色块 + 卡片堆叠 |
| Drafting table + 蓝图标记 | bento grid + 圆角 glassmorphism |

**反命题（贯穿全站）**：
- 不是 chat wrapper —— 是会对辩的量化 agent
- 不是黑盒 LLM 信号源 —— 决策可回放、可审计
- 不是又一个回测框架 —— backtest = paper = live 同份代码
- **不是又一个深色 AI tool 模板** —— 这是一份**被精确绘制的工程图**

**视觉锚（必出现的 5 个母题）**：

1. **Crosshair 注册标记** ✚ —— 出现在主要绘图区四角，1px hairline
2. **Italic display serif numerals** —— 章节序号用 Fraunces italic 200px+，半 bleed
3. **Hairline-only rules** —— 所有分隔线 ≤ 1px；**绝不**用 box-shadow / blur orb / drop-shadow
4. **Title block** —— 工程图角落的元信息块（rev · date · count）
5. **Ticker strip** —— 顶部一行 monospace 滚动信息（commits · stars · system state）

---

## 2. Audience-Driven Principles

三类用户，三种期待，**首页必须同时让三类用户感到"这是给我的"**：

| 用户 | 在意 | 首页对应承诺 |
|---|---|---|
| 量化研究者 | 信号可溯源、回测可信、多市场覆盖 | UnifiedKernel + GlobalCoverage + CurrentState（透明度声明） |
| AI agent 开发者 | multi-agent 编排实战、hooks/permissions/MCP 真用法 | AgentDebateDemo + EngineeringHarness |
| 开源工程师 | AGPL、commit 频率、决策记录、PR 友好度 | CTAFooter + GitHub stats + decision_record 演示 |

**禁忌**：不要预设具体国籍 / 市场 / 资产；不要把语言锁死中或英（用户语言 = 用户最近一条消息的语言）。

---

## 3. Color System

### 3.1 Token 定义（`src/app/globals.css` `@theme` 块）

```css
@theme {
  /* 基底 */
  --color-bg: #0a0e1a;
  --color-bg-deep: #060814;     /* hero / widget 浮起感 */
  --color-bg-elev: #11162a;
  --color-fg: #f5f5f7;
  --color-fg-muted: #9ba3b4;
  --color-border-subtle: #1f2740;

  /* 品牌主调 */
  --color-cyan: #5fb3ff;        /* primary CTA / lineage / accent */
  --color-cyan-dim: #2a5a7a;
  --color-fox-red: #c8463c;     /* 反方 / bear / 风险 / 立场对立 */
  --color-gold: #d4a744;        /* risk gate / 成功告警 / GitHub stars */

  /* 语义色 (D-9 新增) */
  --color-bull: #4ade80;        /* 看涨 / 加仓 / profit (国际惯例) */
  --color-success-dim: #2a7a55;
  --color-line-data: #5fb3ff;   /* = cyan 的语义化别名，data lineage 用 */
}
```

### 3.2 使用规则

| Token | 用在 | **不要用在** |
|---|---|---|
| `cyan` | 主 CTA 按钮、wordmark、链接、eyebrow、cyan glow 球 | 警告、错误、bear |
| `fox-red` | bear agent、反方仓位、risk veto 红光、对立观点 | 普通强调（用 cyan） |
| `gold` | risk gate 通过标记、GitHub stars 数字、AGPL 徽章 | 大面积背景 |
| `bull` (green) | 看涨 agent、profit 数字、success state | 中性提示 |
| `bg-deep` | Hero 主背景、widget 浮起的 base | section 内部 |

**红涨绿跌冲突**：Inalpha 全局采用**国际惯例 green-up / red-down**（bull=green / bear=red）。A 股 fixture 仅在数据层翻译（fixture JSON 内 `display_locale: zh-CN` 时反转），**视觉层永不翻转**。zh 版页面需在 hero / demo 附近加一行小字说明（"全站采用国际惯例 green-up red-down"），fox-red 同时作品牌色与 bear 色不冲突 —— 它是"反方"的统一符号。

### 3.3 透明度规范

仅用 6 档：`/10 /20 /40 /60 /80 /100`。禁用 `/15 /25 /33` 等任意值。

---

## 4. Typography

### 4.1 字体栈（三色印刷：mono + sans + 新增 display serif）

```css
--font-sans:    var(--font-geist-sans),  ui-sans-serif, system-ui, sans-serif;
--font-mono:    var(--font-geist-mono),  ui-monospace, "SF Mono", monospace;
--font-display: var(--font-display),     ui-serif,  "Iowan Old Style", Georgia, serif;
```

`--font-display` = **Fraunces**（variable serif，opsz/SOFT/WONK 全开）。**通过 `next/font/google` 引入**，
在 `apps/web/src/app/[locale]/layout.tsx` 中已配 `--font-display` 变量。

**禁止引入**：JetBrains Mono / Fira Code（双 mono 字体打架）；Inter / Space Grotesk（AI 模板标配）；
任何额外的西文 serif（一套足够）。

### 4.2 用法分工

| 场景 | 字体 | 风格特征 |
|---|---|---|
| Section index `01.` `02.` | **display italic** | Fraunces italic, 160–280px, font-variation `WONK=1` |
| Section title | **display italic** | Fraunces italic, 56–96px, 行高 0.94 |
| Hero / 醒目引述 | display | Fraunces normal or italic |
| Ticker / 顶部状态条 | mono | uppercase, tracking 0.22em |
| 节点 label / 边 label / 工程注释 | mono | uppercase, 11–13px, tracking 0.16em |
| 代码 / 命令 / 数字 / JSON | mono | 自然大小写，font-feature `calt`+`zero` |
| 正文段落 | sans | 默认 |
| 按钮 / 表单 | sans | medium |

**经验法则三段**：
- "**这是工艺品**" → display italic（章节标题、序号、editorial quote）
- "**这是机器产物**" → mono（代码、坐标、标签、tag chip、注释）
- "**这是普通文字**" → sans（段落、按钮、长描述）

### 4.3 号阶

```
xs    11px   → 边 label、坐标注释、ruler tick
sm    13px   → metric label、tag chip
base  15–16px → 正文
lg    20px   → 强调段
xl    28px   → section sub
display-md   clamp(2.5rem, 4vw, 3.5rem)   → 小型 display heading
display-lg   clamp(3rem, 6vw, 5.5rem)     → section title
display-xl   clamp(6rem, 14vw, 14rem)     → section index 数字 (italic)
```

任何 hero wordmark > 8vw 一律走 display 字体（mono 不撑场）。

### 4.4 Font features

```css
body {
  font-feature-settings: "ss01", "cv11", "zero", "calt";
}
.display-italic {
  font-variation-settings: "opsz" 144, "SOFT" 30, "WONK" 1;
  font-feature-settings: "ss01", "ss02";
}
```

`WONK=1` 在 Fraunces 中开启字怀 alternates（`g` 单环、`y` 直尾），辨识度极高，反 generic 关键。

---

## 5. Layout & Spacing

### 5.1 主框

- Content max-width: `max-w-6xl` (1152px) —— 站点上限，**不要 max-w-7xl**
- Hero 例外：`max-w-7xl`（容纳右侧 widget）
- Grid：默认 `grid-cols-12`，section 内容多用 7+5 / 4+4+4 / 6+6 三种切分

### 5.2 间距 step

仅用：`0.5 / 1 / 1.5 / 2 / 3 / 4 / 6 / 8 / 12 / 16 / 24 / 32` (Tailwind unit)。禁用 `2.5 / 3.5 / 5 / 7 / 9 / 11` 等。

### 5.3 Section padding

```
py-24 sm:py-32     # 标准 section 上下 padding
px-6               # 横向（不要 px-4 / px-8）
```

Section 之间**不留多余 margin**（padding 已含）；相邻深色 / 浅深色背景用 `border-y border-border-subtle` + `bg-bg-elev/20` 做层次。

### 5.4 圆角

仅用 `rounded-md (6px)` / `rounded-lg (8px)` / `rounded-xl (12px)` / `rounded-full`。**不要 rounded-2xl / rounded-3xl**（过于 SaaS）。

---

## 6. Motion Language

### 6.1 Preset 矩阵（`src/lib/motion.ts`）

| name | duration | ease | when to use |
|---|---|---|---|
| `fadeUp` | 0.5s | `[0.2, 0, 0.2, 1]` | 普通文本入场（已有） |
| `stagger` | 0.08s delay | — | 子元素串联（已有） |
| `charStagger` | 0.04s delay | — | 字符级入场（已有，仅 wordmark 用） |
| `slideInTilt` | 0.6s | `[0.2, 0, 0, 1]` | DualThesis 双卡左右滑入 + ±1° rotate |
| `typewriter` | 18ms/char | linear | TerminalBlock 命令打字 / decision_record |
| `pathDraw` | 1.2s | `[0.4, 0, 0.2, 1]` | DataLineagePath / DebateGraph SVG path stroke-dasharray |
| `pulseDot` | 2s infinite | ease-in-out | LiveBadge 脉冲点 / pulse-glow 节点 |
| `countUp` | 2s | `[0.2, 0, 0, 1]` | StatCounter 数字滚动 |

### 6.2 触发规则

- **首屏**：`initial="hidden" animate="visible"`（不等 scroll）
- **后续 section**：`whileInView` + `viewport={{ once: true, margin: "-100px" }}`
- **核心 demo (AgentDebateDemo)**：`viewport={{ once: false }}` —— 允许 re-enter 时重放，**配 Replay 按钮**

### 6.3 reduced-motion fallback

```tsx
const prefersReducedMotion = useReducedMotion();
// 所有 motion variants 通过 motion-safe gate
// AgentDebateDemo 在 reduced-motion 下退化为：静态截图 + decision_record 完整文本，不能空白
```

**铁律**：用户关闭 motion 时**不能让 demo 区域变空白**。所有"流光 / typewriter / scroll-trigger"必须有 SSG 兼容的静态退化版。

### 6.4 性能预算

- 首屏 JS bundle ≤ 200KB gzipped
- DebateDemo lazy load（`dynamic(() => import(...), { ssr: false })`）
- **不要 WebGL / canvas**，所有动画 CSS + SVG + motion/react 即可

---

## 7. Component Patterns

> 路径：`apps/web/src/components/primitives/`（已有 3 个 + 新增 7 个）

### 7.1 现有（沿用）

- `<DotGrid fade="radial|top|none" />` —— 背景点阵，新版密度从 28px → 36px
- `<LocaleSwitcher />` —— 右上角 EN/中切换
- `<CopyableCommand command copyLabel copiedLabel />` —— 可复制命令块
- `<Button variant="primary|ghost|link" size="default|lg" />`

### 7.2 新增 7 个 primitive

#### `<TerminalBlock prompt="$ inalpha>" content typewriter? scrollPin? />`

- 黑色背景（`bg-bg-deep`）+ 顶部三个圆点（macOS 风）+ mono 内容
- `typewriter=true` 时配合 `motion` `useAnimate` 控字符
- `scrollPin=true` 时 `position: sticky top-24`（demo 区域用）

#### `<AgentBubble role="bull|bear|research|risk" status="idle|thinking|done" />`

- role 决定边框色：bull→bull / bear→fox-red / research→cyan-dim / risk→gold
- 左上角 role label（mono uppercase）+ status dot（idle=灰 / thinking=pulseDot / done=填色）
- 内容 slot：children 任意 React 节点

#### `<CodeDiff before={['…']} after={['…']} language="python" />`

- 两栏 mono，删行 `bg-fox-red/10 border-l-2 border-fox-red` 加行 `bg-bull/10 border-l-2 border-bull`
- 用于演示 "backtest → live 改 1 行" 这种**视觉锤子**
- 语法高亮**不引入 shiki / prismjs**（bundle 膨胀），用简易 regex 染 keyword / string / number 即可

#### `<StatCounter target={142} prefix="★ " suffix=" stars" duration={2} />`

- motion countUp，进入 viewport 触发
- target 来自 build-time 拉取的 `public/github-stats.json`（CI 跑 `scripts/fetch-github-stats.ts`，离线时 fallback 0 + hidden）

#### `<GlassCard tint="cyan|fox|neutral" />`

- `bg-bg-elev/40 backdrop-blur-md border border-border-subtle rounded-xl p-6`
- tint 决定 hover 边框色：cyan→cyan/40 / fox→fox-red/40 / neutral→fg-muted/40
- 共用：Hero `<LiveDebatePanel>`、DualThesis 双卡、CurrentState 状态卡

#### `<LiveBadge label="alpha quality" />`

- 左侧 pulseDot（gold）+ mono 小字 + `rounded-full border-gold/40 bg-gold/10`
- CurrentState section 顶部用，强化"诚实告知"

#### `<DataLineagePath nodes={[…]} flowing? />`

- SVG path 组件，按 `nodes` 自动算 Catmull-Rom 平滑曲线
- `flowing=true` 时虚线流动（`stroke-dasharray` + `animateMotion`）
- UnifiedKernel section 用（agent → kernel → strategy）

### 7.3 视觉契约（所有 primitive）

| 属性 | 规则 |
|---|---|
| Border | `border-border-subtle` 默认；hover 升至 token 主调 `/40` |
| Padding | 内部 padding `p-4 / p-6 / p-8` 三档，按容器层级 |
| Hover | `transition-colors` 默认 200ms；**不要 transition-all** |
| Focus | `focus-visible:outline-2 focus-visible:outline-cyan focus-visible:outline-offset-2` |
| Disabled | `opacity-50 cursor-not-allowed`，不改变颜色 |

---

## 8. Anti-Patterns（红线，做了就 revert）

**新增 D-9 反 AI-slop 系列**：

❌ **任何 `blur-[…]` 大色块**（cyan/bull/gold blur orb 在 hero 或 section 背景）—— 这是 AI tool 模板第一标识，**彻底禁用**
❌ **`box-shadow: 0 X X X color/N` 软投影** —— 节点 hover / 卡片不许用。要分层只能用 **1px hairline + grain 纸张感**
❌ **`backdrop-blur-*` 玻璃质感卡片** —— 已是 generic AI dashboard 通用语，弃用；改用纯色 + hairline border
❌ **`rounded-xl` / `rounded-2xl` / `rounded-3xl`** —— 卡片圆角 ≤ `rounded-sm` (2px)，**大部分节点 0 圆角**（schematic 要锐角）
❌ **gradient text** 与 **gradient-to-br card background** —— 都已废止
❌ **bento grid 等大色块拼贴** —— 信息层次靠 typographic scale + spacing，不靠色块

**留下 D-8 老红线**：

❌ **巨字 wordmark mono** —— 改走 display serif italic
❌ **emoji 在 hero / section title** —— 全站零 emoji
❌ **感叹号** —— 任何 marketing 文案禁用 "!"
❌ **5 条原则罗列** —— 不堆叠
❌ **"革命性 / 颠覆 / 智能 / 全新一代"**
❌ **生成式 hero illustration** —— 不用 AI 出图当 hero 背景
❌ **3+ 连续灰文段落**
❌ **任意 hex 色 / 任意 opacity**

### 8.1 取代关系

| 旧 SaaS 套路 | Inalpha 取代 |
|---|---|
| Glass card + drop shadow + cyan glow | Hairline outline + 顶 accent 1px line + grain |
| 大 blur orb 背景 | `hairline-grid` (drafting grid) + 角落 crosshair `+` |
| 渐变 hero text | display serif italic + mono sub-line |
| 卡片 grid 平铺 | 不对称 layout + bleed-off italic index 数字 |
| Round-tab pill | 方括号 `[ ALPHA ]` 或 mono uppercase 加 hairline 下划线 |

---

## 9. Voice & Copy Rules

### 9.1 英中调性差异

| 语言 | 调性 | 例 |
|---|---|---|
| EN | engineer-direct, dry humor | "Not a chat wrapper. Agents that argue back." |
| ZH | 简洁、克制、不卖弄 | "Agent 可以吵，记录不会。" |

**禁忌**：
- ZH 别翻译 "first-class" 为 "一流" → 用 "一级公民"
- EN 别用 "leveraging / synergy / cutting-edge"
- ZH 别用 "赋能 / 助力 / 智能化"

### 9.2 段落约束

- 每段 ≤ 80 字（中文）/ ≤ 30 词（英文）
- Hero sub ≤ 40 字 / 15 词
- 任何 section blurb ≤ 80 字

### 9.3 mono vs sans 在文案中的语义分工

| 走 mono | 走 sans |
|---|---|
| 命令 (`$ git clone …`) | 描述 |
| 文件路径 (`apps/web/`) | 概念 |
| 数字 + 单位 (`12 markets`) | 句子 |
| 术语首字符大写 (`Orchestrator`) | 普通名词 |
| Decision record JSON | rationale 文字 |

行内插 mono 用 `<code className="font-mono text-cyan">…</code>`，**不要**靠 ` `` ` markdown 等 build-time 处理（i18n 文案是 JSON，不走 markdown）。

### 9.4 i18n 文案双语同写

开临时 sheet 双列对照（EN / ZH 一行行写），写完 batch 导回 JSON。**禁止**先写 EN 再机器翻译为 ZH —— Inalpha 用户社区中 ZH 占比高，质量必须并列。

---

## 10. Section Recipes（首页 8 节配方）

每节给：结构骨架 + 文案模板 + 动画 cue。AI 工具可直接据此生成符合调性的代码。

### 10.1 `Hero` (id="hero")

```
[wordmark 24px lockup]            [LocaleSwitcher] [StatCounter ★]

<h1 mono 3xl>               ╭─ GlassCard ──────────╮
  {hero.title}               │ <LiveDebatePanel>     │
</h1>                        │  6 case auto-cycle    │
                             │  每 4s crossfade      │
<p sans xl text-fg-muted>    ╰───────────────────────╯
  {hero.subtitle}
</p>

[Button primary $ git clone …] [Button ghost Live demo →]
```

动画：wordmark `fadeUp` + 文案 `stagger`；widget `crossfade` 自动循环（`AnimatePresence`）。

文案模板：title 双句结构 "Agents that {do something}. Records that don't." / "Agent {动作}，记录不会。" 副 1 句锚双定位。

### 10.2 `BlackBoxProblem` (id="problem")

```
01 / Problem

You're not doing quant.
You're trusting a black box.

[3-row comparison table]
  你想要的 | 黑盒 LLM 给的 | Inalpha 给的
  --------|-------------|-------------
  signal  | 一个 score   | 三方辩论
  reason  | "trust me"  | decision record
  replay  | 不可重放      | JSONL 全留痕
```

动画：表格行 `fadeUp` `stagger`；行 hover 时 Inalpha 列 cyan glow。

### 10.3 `DualThesis` (id="thesis")

详见 plan §B.3。两张 `<GlassCard>` slideInTilt 入场；hover 对方变暗 30%。

### 10.4 `AgentDebateDemo` (id="demo")

详见 plan §B.2。核心交互，单独章节，独立 `dynamic import`。fixture 6 case。

### 10.5 `UnifiedKernel` (id="kernel")

```
04 / Kernel

One codebase. Three modes.

[CodeDiff] before: backtest run    after: live run
                 ↓                        ↓
              [DataLineagePath flowing]
              data → orchestrator → paper / research → strategy

[3 GlassCard]  data v0.x   paper v0.x   research v0.x
  (复用旧 KernelCards 的 from … import …)
```

合并旧 TheLoop（拓扑改为 DataLineagePath）与旧 KernelCards。动画：CodeDiff 进入时 staggered 行入场；lineage path `pathDraw`。

### 10.6 `EngineeringHarness` (id="harness")

```
05 / Harness

Inspired by Claude Code. Adapted for trading.

[左大块 TerminalBlock]                [右侧 list]
$ cat .inalpha/permissions.yaml       hooks
                                      permissions
research:                             plan-exec
  allow:                              subagent
    - data.get_bars                   MCP
    - data.get_news                   swarm
  deny:
    - paper.place_order

risk:
  allow: ["*"]
  require_human: ["place_order > 0.5"]
```

左侧 `<TerminalBlock typewriter>`；右侧 chip list hover 时左侧切到对应配置片段。

### 10.7 `GlobalCoverageState` (id="coverage")

```
06 / Coverage          [LiveBadge alpha quality]

Same kernel. Same prompts. Same agents.

[Tag groups]
  Crypto (1):  BTC ETH …
  Equities (9): US 美股 / CN A股 / HK 港股 / JP 日股 / …
  Macro (2):  Global indices / FRED

[StatCounter row]
  ★ 142 stars · 23 contributors · 487 commits · alpha quality
```

合并 MarketCoverage + 新 CurrentState（诚实告知 alpha 阶段）。Tag chip hover cyan。

### 10.8 `CTAFooter` (id="cta")

详见 plan F2。`CopyableCommand` 加 Tabs (`pip install` / `git clone`)；contributors avatars build-time 拉。

---

## 11. AI Prompt Templates（喂给外部 AI 工具）

### 11.1 给 open-design 的 prompt（用于探索性 mockup）

```
You are designing a section for Inalpha — an open-source quant trading framework
where multi-agent LLMs hold opposing positions and every decision is auditable.

Read /Users/mirror/study/Inalpha/apps/web/DESIGN.md fully. Your output MUST:

1. Use only token colors defined in §3.1 (no arbitrary hex).
2. Follow typography rules in §4 (mono for code/commands/numbers, sans for prose).
3. Respect anti-patterns in §8 — no giant wordmarks, no gradient blobs > 1, no emoji.
4. Match voice in §9 — engineer-direct, no marketing fluff.
5. Use one of the recipes in §10 as structural starting point.

Task: design a {{section_name}} section that achieves {{user_goal}}.
Output: Tailwind 4 + motion/react + next-intl(en, zh) React component, single file ≤200 lines.
Provide both en and zh strings inline as JSON, ready to drop into messages/{en,zh}.json.
```

### 11.2 给 Claude Code / Cursor 的 prompt（用于按 DESIGN.md 重写既有 section）

```
@apps/web/DESIGN.md @apps/web/src/components/sections/<target>.tsx

请按 DESIGN.md §10.<n> 重写此 section。约束：
- 字体 / 颜色 / 间距 / 圆角全部走 token，不出现任意值
- 动画走 §6.1 preset，不新增 motion variant（如需新增先改 DESIGN.md）
- 文案双语并写（en + zh），先放进 messages/{en,zh}.json
- 复用 primitive：见 §7.1 / 7.2，缺什么先建 primitive 再用
- 输出：单文件 ≤200 行；i18n key 与现有 namespace 兼容
- 跑通 `pnpm typecheck`；不破坏 `output: "export"` 静态导出
```

### 11.3 验收清单（PR 自检）

提交任何 section 改动前，对照本 checklist 自查：

- [ ] 颜色 / opacity / 圆角 / 间距全部走 token（§3 / §5）
- [ ] mono vs sans 分工正确（§4.2 / §9.3）
- [ ] 没有 §8 红线（grep emoji / 巨字 / 渐变球过多）
- [ ] motion preset 来自 §6.1，reduced-motion 退化已实现
- [ ] 文案双语并写，en/zh 调性符合 §9.1
- [ ] 复用 primitive 而非重造（§7）
- [ ] `pnpm typecheck` 通过
- [ ] `pnpm build` 静态导出成功（不引入 server-only API）
- [ ] DevTools 看首屏 JS ≤ 200KB gzipped（§6.4）

---

## 附录 A. 与项目仓库 CLAUDE.md 的关系

- 仓库根 `CLAUDE.md` §3.2 "全球用户、不预设语言/市场" → 本文件 §2 / §9 落地
- 仓库根 `CLAUDE.md` §3.1 "金融时效性硬约束" → 本文件 §10.4 / §10.7 落地（demo / coverage 不允许出现过期具体数字，所有数字来自 fixture 或 build-time stats）
- 当本文件与仓库 `CLAUDE.md` 冲突时：仓库 CLAUDE.md 优先（本文件是子约束）

## 附录 B. 与 open-design 的解耦

open-design 装在 **仓库外** `/Users/mirror/study/open-design`：
- 不进 `apps/web/package.json`
- 不进 CI
- 它的产物落到 `/Users/mirror/study/open-design/outputs/`，**不进 Inalpha 仓库**
- 选中的 mockup 转 Tailwind 代码时人工移植
- 本文件可直接复制 / symlink 到 open-design 输入区作为 system prompt

## 附录 C. 变更管理

本文件改动需在 PR description 写明：
1. 改了哪一节
2. 为什么改（先 DESIGN.md 再代码，还是代码先行后补文档？后者是 anti-pattern）
3. 受影响的现存 section / primitive 清单

任何"代码与 DESIGN.md 漂移"的 PR 必须二选一：(a) 改回代码对齐 DESIGN.md；(b) 同时更新 DESIGN.md 与代码。
