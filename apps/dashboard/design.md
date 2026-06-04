# Inalpha Operator Console · 设计系统

> 操作者控制台的设计源真相（design source of truth）。
> 改动配色 / 字体 / 间距 / 组件观感前，先读这里；落地实现见 `src/app/globals.css`。

---

## 1. 美学方向：印章终端 / Vermilion Terminal

一台**戴着朱红印章的交易终端**。

控制台是只读运行时看板——账户、持仓、Live Runner、Agent 活动、因子、风控。它要像
专业金融终端那样**信息密集、指标直观、一眼可读**，但拒绝千篇一律的「SaaS 仪表盘」气质：
用一枚朱红印章（`assets/11-logo-stamp-style.png`，狐狸 + α）作签名标记，把工程图纸的
冷静和报刊编辑体的格调揉进同一块盘面。

三条贯穿全局的线索：

1. **印章红（朱红 `--seal`）= 品牌锚点**。页眉、面板、侧栏激活态、KPI 标尺都用它点一下，
   像在文件上盖章。它**不是**涨跌语义色，永远只表身份。
2. **电光青（`--accent`）= 数据与交互**。LIVE 指示、链接、聚焦、图表刻度。
3. **等宽数字 = 终端语感**。所有财务数字 `font-mono` + `tabular-nums`（`.tnum`），列对齐。

---

## 2. 双主题：同一台终端的两种照明

「黑白两主题」= 同一台终端在两种工作场景下的照明，不是简单反色。

| | **Terminal Dark**（默认） | **Broadsheet Light** |
|---|---|---|
| 场景 | 盘中 / intraday，长时间盯盘 | 复盘 / review，阅读与汇报 |
| 气质 | 墨黑终端、电光青、朱红印章 | 报纸暖白、墨黑字、同一枚印章 |
| 背景 | `#080b14` 冷墨黑 | `#f4f1e8` 暖报纸白 |
| 颗粒混合 | `overlay`，弱（0.035） | `multiply`，略强（0.045） |

切换由 `<html data-theme="dark|light">` 驱动：

- 原始色值声明在 `:root` / `[data-theme="light"]`；`@theme inline` 把 Tailwind 的
  `--color-*` 指向这些原始变量。**切 `data-theme` 即整盘换肤**，所有 `bg-*/text-*/border-*`
  工具类（含 `/opacity` 透明度修饰）自动跟随，无需逐组件改。
- 防闪烁：root layout 内联脚本在首帧前读 `localStorage('inalpha-theme')`，无则跟随系统
  `prefers-color-scheme`，兜底 `dark`。`<html>` 默认 `data-theme="dark"` + `suppressHydrationWarning`。
- 切换控件：侧栏底部 `ThemeToggle`（月/日段控），与 `LocaleSwitcher` 同款样式。

---

## 3. 色板（token）

语义名定义在 `:root` / `[data-theme]`，经 `@theme inline` 暴露为 Tailwind 工具类。
**只用语义 token，不写裸 hex。**

| 语义变量 | Tailwind 工具 | Dark | Light | 用途 |
|---|---|---|---|---|
| `--surface` | `bg-bg` | `#080b14` | `#f4f1e8` | 主背景 |
| `--surface-deep` | `bg-bg-deep` | `#04060d` | `#e7e3d6` | 侧栏 / 凹槽 |
| `--surface-elev` | `bg-bg-elev` | `#0f1320` | `#fcfbf5` | 卡片 / 面板 |
| `--ink` | `text-fg` | `#eef1f7` | `#15171d` | 主前景 |
| `--ink-muted` | `text-fg-muted` | `#8089a0` | `#5b6170` | 次级文字 |
| `--hairline` | `border-border-subtle` | `#1b2235` | `#d9d4c5` | 极细分隔线 |
| `--accent` | `*-cyan` | `#5cc6ff` | `#0d6db0` | 数据 / 交互主色 |
| `--seal` | `*-seal` | `#e2533f` | `#c8463c` | **品牌印章红（非涨跌）** |
| `--down` | `*-fox-red` | `#f0584b` | `#c0392c` | 跌 / 拒单 / 错误 |
| `--gold` | `*-gold` | `#e0b03f` | `#9a7416` | 在途 / 警示 |
| `--up` | `*-bull` | `#2fcf8e` | `#0f8a58` | 涨 / 成交 / 运行中 |

**涨跌惯例**：国际惯例 green-up / red-down。数据层若反转在 render 时翻，视觉层永远绿涨红跌。

---

## 4. 字体

| 角色 | 字体 | 用途 |
|---|---|---|
| Display | **Fraunces**（可变 serif，`.display` / `.display-italic`） | 页眉 / 面板序号 / 标题——报刊编辑体格调 |
| Sans | **Geist Sans**（`--font-sans`） | 正文、标签、导航 |
| Mono | **Geist Mono**（`--font-mono`，`.tnum`） | **一切数字**、状态码、序号、时间——终端语感 |

规则：金融数字一律 `font-mono` + `tabular-nums` 保证列对齐；序号 / 状态标签用 mono 大写 +
字距 `tracking-[0.16em]`；标题用 Fraunces 斜体序号 + 正体标题。

---

## 5. 背景与质感（globals.css utilities）

背景保持**干净**——不用网格 / 棋盘纹（曾试过 `hairline-grid` 工程图纸网格，视觉太碎，已弃）。
深度只靠极弱的晕影 + 颗粒：

- `.vignette` — 四角晕影，把视线收向盘面中央（盘口聚焦）。
- `.grain` — 纸张颗粒，暗色 `overlay` / 亮色 `multiply`，加质感不偏色。
- `.seal-glow` — 侧栏印章 logo 的朱红微辉。
- `.tick-accent` — 数据 / 标签前的青色标尺刻度（终端「行号」语感）。
- 滚动条 — 细、暗、hover 转青。

外壳：左侧 `w-60` 固定侧栏（`bg-bg-deep` + 模糊）+ 右侧 `max-w-[1400px]` 内容区，
叠 `vignette` + `grain`，内容 `z-10` 浮于纹理之上。

---

## 6. 组件语汇

- **侧栏** — 顶部印章 logo（`seal-glow`）+ Inalpha 字标；导航为「序号 + 图标 + 名称」的终端行，
  激活态左侧朱红印章刻度 + 青色高亮；底部主题 / 语言段控 + `Build · D-11` 标记。
- **PageHeader** — 朱红印章竖条 + Fraunces 斜体序号 + 标题，下接细线分隔。
- **Panel** — 圆角 hairline 边框 + `bg-bg-elev/40` + 模糊；头部朱红刻度 + 序号 + mono 大写标题。
- **KPI 卡** — 顶部 1px 语义标尺（cyan/bull/seal），标签带青色刻度，特大 mono 数字，hover 转青边。
- **StatusBadge** — 语义色 pill：成交/运行绿、拒单/错误红、在途金、信息青、其余灰；running 带脉冲点。
- **LiveStrip** — LIVE 绿点（刷新时脉冲）+ 数据时间 + 相对时间；后端离线切红「显示上一帧」。
- **图表**（lightweight-charts）— canvas 不认 `color-mix`，故从原始主题变量（纯 hex）实时读色，
  `MutationObserver` 监听 `data-theme` 换肤即重绘。

---

## 7. 动效

克制、有目的，但**不死板**——盘面要有「在跑」的呼吸感。`prefers-reduced-motion`
下所有装饰性动效（含 motion 入场、印章呼吸、`.rise`）一律关停。

入场 / 生命感：

- `.rise` — 元素上浮淡入（0.5s）。Panel 默认带，页面切换时错落揭示。
- KPI 卡 — `motion`（Framer Motion v12，`motion/react`）做错落入场（`delay: i*0.07`）。
- `.seal-glow` — 侧栏印章朱红光晕 5s 缓慢呼吸。

交互反馈：

- KPI 卡 — 悬浮上抬 3px + 青色投影 + 边框转青（motion `whileHover`）。
- 侧栏导航 — 悬浮右移 2px + 变色；激活态左侧朱红刻度。
- 侧栏 logo — 悬浮放大 + 轻微逆时针。
- 换肤 0.4s 颜色过渡（不动布局）。

实时数据：

- LIVE 脉冲点（数据刷新瞬间）、`.flash-cyan`（新帧到达轻闪）、`.caret-blink`。

> 约束:hover 位移幅度 ≤3px;入场只在挂载触发,不随 SWR 刷新重放;
> 动效用 `motion`(已装)或纯 CSS,二者都必须在 reduced-motion 下退化为静态。

---

## 8. 硬约束

- **面向全球用户**：不在组件里写死中英文，文案走 `next-intl`（`messages/*.json`）。
- **印章红 ≠ 涨跌色**：`--seal` 只表品牌，涨跌用 `--up` / `--down`。
- **不写裸 hex**：一律用语义 token / Tailwind 工具类，新主题才能自动覆盖。
- **新增交互态**：必须在 dark + light 两主题下都验证对比度可读。
- **可点击必手型**：所有可点击交互（button / a / `[role=button]` / label / summary / select）
  鼠标光标一律 `cursor: pointer`，禁用态 `not-allowed`。已在 `globals.css` base 层全局兜底，
  组件不必逐个加；新增自定义可点击元素若非上述标签，需自行补 `cursor-pointer`。
