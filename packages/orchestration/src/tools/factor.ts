/**
 * services/factor 的 Mastra tool 包装。
 *
 * 接现成因子库（pandas-ta / WorldQuant Alpha101 / qlib Alpha158）+ 自实现有效性打分
 * （前瞻收益分位 / 时序 Rank IC）。让 agent 能基于**经验证有效的因子**做分析与择时，
 * 而不是对着 5 个写死指标编叙事（见 docs/miro/11）。
 */
import { createTool } from "@mastra/core/tools";
import { z } from "zod";

import { resolveRequestToken } from "../auth.js";
import { FactorClient } from "../clients/factor.js";
import { getSettings } from "../config.js";
import {
  DiscoveryInputSchema,
  DiscoveryOutputSchema,
} from "../mastra/workflows/factor-discovery.js";
import { EvolutionInputSchema, EvolutionOutputSchema } from "../mastra/workflows/factor-evolution.js";

// 只列 factor engine 真正支持的周期（_tf_seconds）。1mo/1q/1y 引擎不识别会按 1h 误算
// 窗口，且月/季/年线 bar 太少算不出有意义的有效性，故不暴露给 agent。
const TimeframeSchema = z.enum([
  "1m", "5m", "15m", "30m", "1h", "2h", "4h", "1d", "1wk",
]);

const SymbolSchema = z
  .string()
  .min(1)
  .max(50)
  .regex(
    /^[\^A-Za-z0-9._/\-:]+$/,
    "symbol 不能为空 / 含空格；crypto 'BTC/USDT' / 股票 'AAPL' / 指数 '^N225' / akshare 'sh.600519'",
  );

type ToolRequestContext = { authToken?: string; get?: (key: string) => unknown };

async function getClient(ctx?: ToolRequestContext): Promise<FactorClient> {
  const settings = getSettings();
  const token = await resolveRequestToken(ctx);
  return new FactorClient({ baseUrl: settings.factorServiceUrl, token });
}

// ────────────────────────────────────────────────────────────────────
// factor.timing —— 主力：当前对该标的有效的因子 + 方向
// ────────────────────────────────────────────────────────────────────

export const factorTimingTool = createTool({
  id: "factor.timing",
  description: `
    对一个标的 / 周期，返回**当前最有效的若干因子**（按时序 Rank IC 排序）及其读数、
    方向、强度。这是"用有效因子做择时"的主入口——给的是数据背书，不是 LLM 叙事。

    何时用：
    - 用户问"现在该不该买/卖""什么信号""怎么择时""有什么有效因子"
    - 设计策略 / 下单前，想知道"当下哪些因子真的预测了后市"（喂 author_strategy / create_plan 的依据）
    - 想用真因子值替代凭感觉的技术判断

    何时不用：
    - 只要 K 线原始走势 → data.get_bars
    - 要完整多 analyst 研究（基本面 + 情绪 + 辩论）→ research.deep_dive（factor.timing 是其中"技术有效性"那一块的加强版）
    - 全市场扫描 N 个标的 → 单次只查一个标的，别在 loop 里滥用

    返回 top_factors[]：每个含 name / kind / value（最新读数）/ rank_ic（越大越有效，正=因子高→后市涨）/
    rank_ic_recent（近 1/3 窗 IC，与 rank_ic 反号或趋零=因子正在衰减，引用时要降权并说明）/
    decay_state（服务端衰减三态 stable/fading/decaying，ADR-0047——设计策略时连同 rank_ic
    一起填进 paper.author_strategy 的 factorContext 建血缘；decaying 的因子别当核心信号）/
    turnover（0-1 换手，高 IC+高换手的信号实盘打折）/ direction（+1 看多 / -1 看空 / 0 无效）/
    strength（0-1）/ low_confidence / corr_pruned（被它挤掉的同质因子，top-N 已去相关）。
    available=false 或 top 为空时说明该标的样本不足，**别硬编故事**，如实告诉用户数据不够。

    坑：
    - rank_ic 是历史统计有效性，非未来保证；direction 只在 |rank_ic| 过阈值才非 0
    - candidates_evaluated 是"top-N 从多少候选里挑的"——候选几十个时最高 |IC| 天然偏乐观
      （多重检验），引用时别把 top1 的 IC 当确定性结论
    - ic_null_benchmark 把上面这条定量化：纯噪声在同样候选数/样本量下能跑出的期望最大
      |IC|。top1 的 |rank_ic| 没显著高于它 ⇒ 可能只是选择效应，措辞要降级（"未显著强于
      噪声基准"）；它是地板不是检验，高于它也不等于必然有效
    - lookbackBars 太小 → low_confidence；horizonBars 决定"预测多远的收益"（默认 5 根）
    - 宏观因子（macro.*：利率 / 期限利差 / 信用利差 / CPI / 就业 / 实体经济 / 情绪等）
      **仅在 timeframe=1d/1wk 返回**；默认 1h 不含 macro。要看标的相对宏观环境的因子，
      显式传 timeframe="1d"（股票 / 指数本就按市场表用 1d，自动含 macro）
  `.trim(),
  inputSchema: z.object({
    venue: z.string().default("binance"),
    symbol: SymbolSchema,
    timeframe: TimeframeSchema.default("1h"),
    asOf: z
      .string()
      .datetime()
      .optional()
      .describe("评估截止时刻 ISO 8601（只用 <= asOf 的 bar）；省略=现在"),
    lookbackBars: z
      .number()
      .int()
      .min(120)
      .max(10000)
      .default(720)
      .describe("向前取多少根 bar 算有效性；越多越稳，太少会 low_confidence"),
    horizonBars: z
      .number()
      .int()
      .min(1)
      .max(60)
      .default(5)
      .describe("前瞻收益窗口（预测未来 N 根 bar 的累计收益）"),
    topN: z
      .number()
      .int()
      .min(1)
      .max(30)
      .optional()
      .describe("返回前几名有效因子；省略=服务端默认（约 10）"),
  }),
  execute: async (inputData, ctx) => {
    const tc = ctx?.requestContext as ToolRequestContext | undefined;
    const client = await getClient(tc);
    return await client.snapshot({
      venue: inputData.venue ?? "binance",
      symbol: inputData.symbol,
      timeframe: inputData.timeframe ?? "1h",
      asOf: inputData.asOf,
      lookbackBars: inputData.lookbackBars ?? 720,
      horizonBars: inputData.horizonBars ?? 5,
      topN: inputData.topN,
    });
  },
});

// ────────────────────────────────────────────────────────────────────
// factor.score —— 深挖：指定因子的完整有效性（分位前瞻收益 + ICIR）
// ────────────────────────────────────────────────────────────────────

export const factorScoreTool = createTool({
  id: "factor.score",
  description: `
    对**指定的一组因子**算完整有效性：时序 Rank IC、ICIR（稳定性）、分位前瞻收益、
    long-short。比 factor.timing 更细，用于深挖某几个因子到底灵不灵。

    何时用：
    - factor.timing 看到某因子有戏，想看它的分位收益结构 / 跨段稳定性
    - 用户点名某类因子（按意图识别：问某指标/因子在某标的上是否有效，
      任何市场任何品种均适用，不限示例里的写法）

    何时不用：
    - 只想要"当下该看哪些因子" → factor.timing（已自动排序取 top-N）
    - 不知道有哪些因子可选 → 先 factor.catalog

    坑：
    - factorIds 省略 = 算全部可时序计算因子（720 bar × 53 因子,显著慢）。
      建议先 catalog 选，再指定
    - low_confidence=true 的因子读数不可据此择时（样本不足）
    - asOf 是"真现在"或用户指定的评估时点，不要用训练记忆里的行情推断
    - 宏观因子（macro.*）**仅在 timeframe=1d/1wk 才会计算**；默认 1h 时即使 factorIds 点名
      macro.* 也会被跳过。要评估宏观因子，传 timeframe="1d"
  `.trim(),
  inputSchema: z.object({
    venue: z.string().default("binance"),
    symbol: SymbolSchema,
    timeframe: TimeframeSchema.default("1h"),
    asOf: z.string().datetime().optional(),
    lookbackBars: z.number().int().min(120).max(10000).default(720),
    horizonBars: z.number().int().min(1).max(60).default(5),
    quantiles: z.number().int().min(2).max(10).default(5),
    factorIds: z
      .array(z.string())
      .optional()
      .describe("要算的因子 id（来自 factor.catalog）；省略=全部可时序因子"),
  }),
  execute: async (inputData, ctx) => {
    const tc = ctx?.requestContext as ToolRequestContext | undefined;
    const client = await getClient(tc);
    return await client.score({
      venue: inputData.venue ?? "binance",
      symbol: inputData.symbol,
      timeframe: inputData.timeframe ?? "1h",
      asOf: inputData.asOf,
      lookbackBars: inputData.lookbackBars ?? 720,
      horizonBars: inputData.horizonBars ?? 5,
      quantiles: inputData.quantiles ?? 5,
      factorIds: inputData.factorIds,
    });
  },
});

// ────────────────────────────────────────────────────────────────────
// factor.panel_score —— 横截面选股：一篮子标的按因子排序 + 横截面有效性
// ────────────────────────────────────────────────────────────────────

export const factorPanelScoreTool = createTool({
  id: "factor.panel_score",
  description: `
    给**一篮子标的（universe）**算每个因子的**横截面有效性**（横截面 rank-IC）+ 最近
    一期的**横截面排名**。这是"在一组标的里按因子选标的"的入口，与 factor.timing/score 的
    **单标的择时**正交：那边判"这一只该不该买"，这里判"这一篮子里挑哪只"。
    （适用任何市场 / 任何品种 / 任何因子——股票 / ETF / 指数 / 加密皆可，不预设市场或因子。）

    何时用（按**意图**判，不锁具体措辞或语言）：
    - 用户给一组标的、要在其中**按某因子排序 / 选最优 / 轮动**（无论中英文、无论哪个市场或
      因子：value / momentum / volatility / 任意 catalog 因子）
    - 想知道某因子在这组标的上**横截面**有没有选股力（每期排序 vs 跨标的后市收益）
    - "成分股里按因子轮动"类策略的选标的步——传 indexCode（如 000300）让它取 **PIT 成分**
      当 universe（去存活者偏差，venue 配 akshare），而非自己列 symbols

    何时不用：
    - 只有一个标的、判方向/时机 → factor.timing（横截面要 ≥2 个标的）
    - universe 二选一：显式 symbols（非 PIT）或 indexCode（PIT 成分）；自由"全市场扫描选池"
      仍不支持——indexCode 只覆盖已快照的指数
    - 要完整研究单个标的 → research.deep_dive

    返回 factors[]（按 |cross_sectional_ic| 排序）：每个含 cross_sectional_ic（横截面 rank-IC，
    正=因子值高的标的后市更强）/ icir / n_periods / low_confidence / latest_ranking[]
    （最近一期排名,按因子值**升序**：取最低=列表首,最高=列表尾——直接拿来选标的）。

    坑：
    - **universe 非 PIT**（is_pit=false 恒成立）：用的是你给的"今天这组标的",历史成分快照
      未建,带**存活者偏差**——别拿历史横截面 IC 当确定性结论,措辞要带这层降级
    - **数据走缓存(fresh=false,不逐标的 backfill)**：判新鲜看 latest_bar_ts[symbol] 距 now 的
      间隔,**不要看 bars_used 数量**(5 根可能全是上周的)。某标的 latest_bar_ts 明显滞后时,
      横截面排名含陈数据,如实说明或让用户先补数据;factors=[] 且 reason 提到 min_symbols =
      有效标的不够(补标的/降 minSymbols),不是"无信号"
    - macro 因子不参与（全市场单值,某时刻对所有标的相同,无横截面区分度）
    - symbols 应同 venue/timeframe；不同市场交易时段不同,缺口留 NaN、某期有效标的不足
      min_symbols 时该期不排名
    - cross_sectional_ic 同样受多重检验影响（ic_null_benchmark 是噪声地板,读法同 factor.score）
    - **unknown_factor_ids 非空 = 你传的某些 factorIds 拼错/过期(不在 catalog)**,已被拒;
      即使 factors 非空也要检查它,别以为传的因子都算了——先 factor.catalog 核对再重试
    - 股票/指数选股按市场表用 timeframe=1d（默认）
  `.trim(),
  inputSchema: z.object({
    venue: z.string().min(1).describe("数据源（按市场分类选，不预设默认市场）"),
    symbols: z
      .array(SymbolSchema)
      .max(50)
      .optional()
      .describe(
        "显式 universe（2-50 个，同 venue/timeframe）。**非 PIT**（调用方给定，带存活者偏差）。" +
        "与 indexCode 二选一",
      ),
    indexCode: z
      .string()
      .optional()
      .describe(
        "指数代码（如 000300），由 data 解析 asOf 那刻的 **PIT 成分**当 universe（is_pit=true，" +
        "去存活者偏差，venue 配 akshare）。与 symbols 二选一,优先 indexCode。取不到 PIT 快照→空+降级",
      ),
    timeframe: TimeframeSchema.default("1d"),
    asOf: z
      .string()
      .datetime({ offset: true })
      .optional()
      .describe("评估截止时刻（只用 <= asOf 的 bar）；省略=现在"),
    lookbackBars: z.number().int().min(120).max(10000).default(720),
    horizonBars: z.number().int().min(1).max(60).default(5),
    minSymbols: z
      .number()
      .int()
      .min(2)
      .max(50)
      .default(3)
      .describe("某期参与横截面排名的最少有效标的数；不足则该期不排名"),
    factorIds: z
      .array(z.string())
      .optional()
      .describe("要算的因子 id（来自 factor.catalog）；省略=全部价量/横截面因子（macro 不参与）"),
  }).refine(
    (d) => d.indexCode !== undefined || (d.symbols !== undefined && d.symbols.length >= 2),
    { message: "提供 indexCode（PIT 成分）或 symbols（≥2 个）二选一" },
  ),
  execute: async (inputData, ctx) => {
    const tc = ctx?.requestContext as ToolRequestContext | undefined;
    const client = await getClient(tc);
    return await client.panelScore({
      venue: inputData.venue,
      symbols: inputData.symbols,
      indexCode: inputData.indexCode,
      timeframe: inputData.timeframe ?? "1d",
      asOf: inputData.asOf,
      lookbackBars: inputData.lookbackBars ?? 720,
      horizonBars: inputData.horizonBars ?? 5,
      minSymbols: inputData.minSymbols ?? 3,
      factorIds: inputData.factorIds,
    });
  },
});

// ────────────────────────────────────────────────────────────────────
// factor.catalog —— 列出可用因子
// ────────────────────────────────────────────────────────────────────

export const factorCatalogTool = createTool({
  id: "factor.catalog",
  description: `
    列出因子库里所有因子定义（id / 来源 / kind / 是否需要 universe / 是否已启用）。

    何时用：
    - 用户问"有哪些因子可用""支持什么指标"
    - 用 factor.score 前先看有哪些 id 可选

    何时不用：
    - 只想知道"现在哪些因子有效" → factor.timing（直接给有效性排序，不用先 catalog）

    来源：pandas_ta（技术指标）/ alpha101（WorldQuant 101，部分横截面项 needs_universe=true 本期不算）/
    qlib_alpha158（Alpha158 风格公式因子，纯 pandas 本地算，默认启用）/
    macro（FRED 宏观：利率/期限利差/美元/VIX，**仅 1d/1wk timeframe 计算**——intraday 请求会被
    跳过，见 extras.timeframes；data 服务缺 FRED key 时自动缺席）。

    坑：
    - 目录是静态定义,**不含有效性**——"列出来"≠"现在灵",灵不灵要 factor.timing/score
    - needs_universe=true 的因子单标的时序模式只是近似,解读时要打折
    - available=false 的源(如 qlib 关闭)因子仍会列出但算不了
  `.trim(),
  inputSchema: z.object({}),
  execute: async (_inputData, ctx) => {
    const tc = ctx?.requestContext as ToolRequestContext | undefined;
    const client = await getClient(tc);
    return await client.catalog();
  },
});

// ────────────────────────────────────────────────────────────────────
// factor.evaluate_candidate —— D-12 · 因子发现 L1：自定义表达式因子评估
// ────────────────────────────────────────────────────────────────────

export const factorEvaluateCandidateTool = createTool({
  id: "factor.evaluate_candidate",
  description: `
    评估一个**自定义因子表达式**（受限 qlib 风格 DSL）：服务端白名单审计 → 在真实
    bar 上求值 → 完整有效性（rank_ic / icir / decay_state / p 值）→ 与库内因子去相关
    对比，一次调用出全套。

    何时用：
    - 用户/你提出了一个因子假设（"放量大阳后often回吐"），想把它形式化成表达式验证
    - 因子发现流程的单次评估步（批量验证走 factor.run_discovery workflow，那里强制 BH 校正）

    何时不用：
    - 评估库里已有的因子 → factor.score（不用重新写表达式）
    - 写交易策略（有持仓/下单逻辑）→ paper.author_strategy；表达式只是"序列→序列"的信号

    表达式语法（恰好是 Python 表达式子集，但只有白名单算子可用）：
    - 列引用：$close / $open / $high / $low / $volume
    - 算子：Ref(s,n) 取 n 根前值 / Delta(s,n) / Mean(s,w) / Std(s,w) / Sum / Max / Min /
      EMA / WMA / Corr(a,b,w) / Rank(s,w) / Quantile(s,w,q) / Abs / Log / Sign /
      Greater / Less / If(cond,a,b)；四则运算与比较直接写
    - 示例：($close - Ref($close, 5)) / Ref($close, 5)（5 根动量）；
      If($volume > Mean($volume, 20) * 2, Sign(Delta($close, 1)), 0)（放量方向）

    硬约束（违反 → 400，按 message 改写）：
    - Ref/Delta 的 lag 必须**正整数**——负 lag = 看未来，直接拒
    - 统计算子必须带 window 字面量（1..500）——没有全样本版，防归一化泄漏
    - 表达式 ≤ 2KB、复杂度有上限；只能引用 OHLCV 列

    返回读法：
    - factor.rank_ic / decay_state 等与 factor.score 同口径
    - ic_pvalue 是参考量级非严格检验；**多次尝试表达式要自报累计次数**——试 30 个
      总有一个 p 小，这是多重检验作弊，propose 前会做批内 BH 校正
    - is_likely_redundant=true（与库内因子 |spearman|≥0.85）= 已有因子换皮，别 propose，
      top_correlated 告诉你撞了谁

    坑：
    - 单标的单周期的 IC 不代表普适有效；换标的/周期重测再下结论
    - 评估不落库；想进候选池走 factor.propose（需要 hypothesis 经济学故事）
  `.trim(),
  inputSchema: z.object({
    expression: z
      .string()
      .min(2)
      .max(2000)
      .describe("受限 DSL 表达式（见 description 语法段）"),
    name: z.string().max(120).optional().describe("人话名（缺省用表达式截断）"),
    venue: z.string().min(1).describe("数据源（按市场分类选，不预设默认市场）"),
    symbol: SymbolSchema,
    timeframe: TimeframeSchema.default("1h"),
    asOf: z
      .string()
      .datetime({ offset: true })
      .optional()
      .describe("评估截止时刻（历史分析用）；缺省 = 现在"),
    lookbackBars: z.number().int().min(120).max(10000).default(720),
    horizonBars: z.number().int().min(1).max(60).default(5),
  }),
  execute: async (inputData, ctx) => {
    const tc = ctx?.requestContext as ToolRequestContext | undefined;
    const client = await getClient(tc);
    const result = await client.customScore({
      expression: inputData.expression,
      name: inputData.name,
      venue: inputData.venue,
      symbol: inputData.symbol,
      timeframe: inputData.timeframe ?? "1h",
      asOf: inputData.asOf,
      lookbackBars: inputData.lookbackBars,
      horizonBars: inputData.horizonBars,
    });

    // P0: 因子评估后自动跑 WalkForward 回测闭环
    const btResult = await client.backtestScore({
      expression: inputData.expression,
      name: inputData.name,
      venue: inputData.venue,
      symbol: inputData.symbol,
      timeframe: inputData.timeframe ?? "1h",
      asOf: inputData.asOf,
      lookbackBars: inputData.lookbackBars,
      horizonBars: inputData.horizonBars,
    }).catch(() => null);

    // P3: 判断演化潜力——IC 有潜力但未过门限时标注
    const evolutionPotential: {
      suggest: boolean;
      reason: string | null;
    } = { suggest: false, reason: null };
    if (result.available && result.factor && result.ic_pvalue != null) {
      const ic = Math.abs(result.factor.rank_ic);
      const pval = result.ic_pvalue;
      // 条件：IC 在 0.02-0.06 之间（有潜力但不够强），且 p < 0.2（不是纯噪声），
      // 且不是 redundant（换了也白换）
      if (ic >= 0.02 && ic < 0.06 && pval < 0.2 && !result.is_likely_redundant) {
        evolutionPotential.suggest = true;
        evolutionPotential.reason =
          `Rank IC=${ic.toFixed(4)} 有潜力但未过强信号阈值（0.06）。` +
          `尝试调整窗口参数、更换核心算子或添加辅助过滤后可能提升。`;
      }
    }

    return {
      ...result,
      backtest: btResult?.backtest ?? null,
      evolution_potential: evolutionPotential,
    };
  },
});

// ────────────────────────────────────────────────────────────────────
// factor.propose / factor.list_candidates —— 候选池（register 门只在 UI）
// ────────────────────────────────────────────────────────────────────

export const factorProposeTool = createTool({
  id: "factor.propose",
  description: `
    把**通过评估的**自定义因子表达式提进候选池（status=pending_review）。
    之后由**人工**在 dashboard 审核——register 后才进 catalog 成为生产因子；
    你没有任何把候选转正的工具（register 门，ADR-0019）。

    何时用：
    - factor.evaluate_candidate 结果像样（|rank_ic| 高于 ic_null_benchmark、
      is_likely_redundant=false、decay_state 非 decaying）且你能讲出经济学故事
    - factor.run_discovery workflow 的幸存者会自动走这里，单发评估后手动 propose 也行

    何时不用：
    - 评估结果平庸 / is_likely_redundant=true（已有因子换皮）——别灌垃圾进审核队列
    - 还没评估过 → 先 factor.evaluate_candidate

    硬要求：
    - hypothesis ≥ 20 字：**为什么**该有效（行为偏差 / 结构性约束 / 信息扩散…），
      只有数字没有故事的候选不收
    - nTested **如实自报**本次会话累计试过多少个表达式——审核人靠它还原选择效应
      背景（试 30 个挑 1 个的 IC 要打很大折扣）；谎报 = 污染审计链
    - testResults 把 evaluate_candidate 的关键产物带上（rank_ic / icir / decay_state /
      max_corr / ic_pvalue），审核人不用重跑

    幂等：同表达式重复 propose 返已有候选（created=false），不重复落。
  `.trim(),
  inputSchema: z.object({
    expression: z.string().min(2).max(2000),
    hypothesis: z
      .string()
      .min(20)
      .max(2000)
      .describe("经济学故事：为什么这个因子该有效"),
    name: z.string().max(120).optional(),
    venue: z.string().optional().describe("评估上下文（复核用）"),
    symbol: z.string().optional(),
    timeframe: z.string().optional(),
    testResults: z
      .record(z.string(), z.unknown())
      .optional()
      .describe("evaluate_candidate 的关键产物（rank_ic/icir/decay_state/max_corr/ic_pvalue）"),
    batchId: z.string().uuid().optional().describe("L1 批次 id（workflow 传入）"),
    nTested: z
      .number()
      .int()
      .min(1)
      .max(10_000)
      .default(1)
      .describe("本批/本会话累计评估过的表达式数（BH 校正的 m，如实自报）"),
  }),
  execute: async (inputData, ctx) => {
    const tc = ctx?.requestContext as ToolRequestContext | undefined;
    const client = await getClient(tc);
    return await client.proposeCandidate({
      expression: inputData.expression,
      hypothesis: inputData.hypothesis,
      name: inputData.name,
      venue: inputData.venue,
      symbol: inputData.symbol,
      timeframe: inputData.timeframe,
      testResults: inputData.testResults,
      batchId: inputData.batchId,
      nTested: inputData.nTested,
    });
  },
});

export const factorListCandidatesTool = createTool({
  id: "factor.list_candidates",
  description: `
    列因子候选池（按 status 过滤：pending_review / registered / rejected）。

    何时用：用户问"提过哪些因子候选 / 哪些还没审 / 哪些已注册"；propose 前查重。
    何时不用：想把候选转正 → 没有这样的 tool，告诉用户去 dashboard /factors 审核。
    坑：registered 因子已自动进 factor.catalog（id 形如 custom.<hash>），择时直接用
    factor.timing/score 查它，不用再来这里。
  `.trim(),
  inputSchema: z.object({
    status: z.enum(["pending_review", "registered", "rejected"]).optional(),
    limit: z.number().int().min(1).max(200).default(50),
  }),
  execute: async (inputData, ctx) => {
    const tc = ctx?.requestContext as ToolRequestContext | undefined;
    const client = await getClient(tc);
    return await client.listCandidates({
      status: inputData.status,
      limit: inputData.limit,
    });
  },
});

// ────────────────────────────────────────────────────────────────────
// factor.run_discovery —— L1 批量验证 workflow 入口
// ────────────────────────────────────────────────────────────────────

export const factorRunDiscoveryTool = createTool({
  id: "factor.run_discovery",
  description: `
    批量验证自定义因子表达式（**L1 强制 pipeline**）：validate（fail-fast）→ 并发
    评估（concurrency 4 打 /custom/score）→ **批内 BH 多重检验校正**（m=批大小）→
    冗余剪枝（与库 |spearman|≥0.8 砍）+ 衰减/低置信门 → 幸存者自动 factor.propose
    （带 batch_id + n_tested 审计锚点，仍需人工 register）。

    何时用：
    - 一次有 2-10 个因子假设要系统验证
    - 用户说"帮我从这些想法里筛出能用的因子"

    何时不用：
    - 只有 1 个表达式 → factor.evaluate_candidate
    - 还没形成表达式 → 先对话把假设形式化

    输入要点：
    - 每个 candidate 必须自带 hypothesis（≥20 字经济学故事）
    - propose=false 可 dry-run（只打分不落候选池）
    - maxAdjustedP 默认 0.1 / maxLibraryCorr 默认 0.8

    输出：
    - verdicts 每条带 outcome + detail 原因
    - proposed 的候选去向：dashboard /factors 候选区块等人工审核
  `.trim(),
  inputSchema: DiscoveryInputSchema,
  outputSchema: DiscoveryOutputSchema,
  execute: async (inputData, ctx) => {
    const mastra = ctx?.mastra;
    if (!mastra) {
      throw new Error("factor.run_discovery: mastra ctx missing (cannot reach workflow)");
    }
    const wf = mastra.getWorkflow("factor_discovery");
    const run = await wf.createRun();
    const result = await run.start({ inputData });
    if (result.status !== "success") {
      throw result.status === "failed"
        ? result.error
        : new Error(`factor_discovery workflow status: ${result.status}`);
    }
    return result.result as z.infer<typeof DiscoveryOutputSchema>;
  },
});

/** P3 · 因子演化闭环：对单个表达式自动迭代改进。 */
export const factorEvolveTool = createTool({
  id: "factor.evolve",
  description: `
    对**有潜力但未过门限**的因子表达式自动迭代改进：生成 2-3 变体 → 评估 → 选最优
    → 最多 N 轮。返回演化链（家谱）和最终最优表达式。

    何时用：
    - factor.evaluate_candidate 返回 evolution_potential.suggest=true
    - 用户说"这个因子还能优化吗" "试试其他参数"

    何时不用：
    - 因子已经很强（|rank_ic| > 0.06）→ 直接 propose
    - 因子已经 redundant → 换方向

    输出：
    - steps[] 演化链（每步表达式 + IC + 变异描述）
    - best_expression 最优表达式 + final_rank_ic
    - improvement_pct 对比原始 IC 的提升百分比
    - n_rounds 总演化轮数
  `.trim(),
  inputSchema: EvolutionInputSchema,
  outputSchema: EvolutionOutputSchema,
  execute: async (inputData, ctx) => {
    const mastra = ctx?.mastra;
    if (!mastra) {
      throw new Error("factor.evolve: mastra ctx missing (cannot reach workflow)");
    }
    const wf = mastra.getWorkflow("factor_evolution");
    const run = await wf.createRun();
    const result = await run.start({ inputData });
    if (result.status !== "success") {
      throw result.status === "failed"
        ? result.error
        : new Error(`factor_evolution workflow status: ${result.status}`);
    }
    return result.result as z.infer<typeof EvolutionOutputSchema>;
  },
});

export const factorTools = [
  factorTimingTool,
  factorScoreTool,
  factorPanelScoreTool,
  factorCatalogTool,
  factorEvaluateCandidateTool,
  factorProposeTool,
  factorListCandidatesTool,
  factorRunDiscoveryTool,
  factorEvolveTool,
] as const;
