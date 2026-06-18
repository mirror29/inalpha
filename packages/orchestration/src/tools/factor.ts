/**
 * services/factor 的 Mastra tool 包装。
 *
 * 接现成因子库（pandas-ta / WorldQuant Alpha101 / qlib Alpha158）+ 自实现有效性打分
 * （前瞻收益分位 / 时序 Rank IC）。让 agent 能基于**经验证有效的因子**做分析与择时，
 * 而不是对着 5 个写死指标编叙事（见 docs/miro/11）。
 */
import { createTool } from "@mastra/core/tools";
import { z } from "zod";

import { defaultServiceSubject, mintServiceToken } from "../auth.js";
import { FactorClient } from "../clients/factor.js";
import { getSettings } from "../config.js";
import {
  DiscoveryInputSchema,
  DiscoveryOutputSchema,
} from "../mastra/workflows/factor-discovery.js";

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
    /^[\^A-Za-z0-9._/-]+$/,
    "symbol 不能为空 / 含空格；crypto 'BTC/USDT' / 股票 'AAPL' / 指数 '^N225' / akshare 'sh.600519'",
  );

type ToolRequestContext = { authToken?: string };

async function getClient(ctx?: ToolRequestContext): Promise<FactorClient> {
  const settings = getSettings();
  const token = ctx?.authToken ?? (await mintServiceToken({ sub: defaultServiceSubject() }));
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
    return await client.customScore({
      expression: inputData.expression,
      name: inputData.name,
      venue: inputData.venue,
      symbol: inputData.symbol,
      timeframe: inputData.timeframe ?? "1h",
      asOf: inputData.asOf,
      lookbackBars: inputData.lookbackBars,
      horizonBars: inputData.horizonBars,
    });
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
    - 一次有 2-10 个因子假设要系统验证——**不要**在 loop 里逐个调
      factor.evaluate_candidate 然后手挑 p 最小的（那正是 BH 要防的作弊）
    - 用户说"帮我从这些想法里筛出能用的因子"

    何时不用：
    - 只有 1 个表达式 → factor.evaluate_candidate 单发（评估完自行决定是否 propose）
    - 还没形成表达式（只有模糊想法）→ 先对话把假设形式化

    输入要点：
    - 每个 candidate 必须自带 hypothesis（≥20 字经济学故事）——过不了 propose 门的
      表达式连评估都别浪费
    - propose=false 可 dry-run（只打分不落候选池）
    - maxAdjustedP 默认 0.1 / maxLibraryCorr 默认 0.8，一般不用动

    输出读法：
    - verdicts 每条带 outcome（proposed / rejected_adjusted_p / rejected_redundant /
      rejected_decaying / rejected_low_confidence / rejected_eval_failed）+ detail 原因
    - **rejected 不是失败**——pipeline 把不该进候选池的挡住了，向用户如实转述原因
    - proposed 的候选去向：dashboard /factors 候选区块等人工审核；你没有转正工具

    坑：
    - 批里混一条明显 lookahead（负 lag）→ 整批 fail-fast 拒绝（修了重提）
    - 同批重复表达式自动去重；跨批重复 propose 幂等返老行
    - 单标的单周期验证，通过≠普适——重要候选换标的/周期再跑一批
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

export const factorTools = [
  factorTimingTool,
  factorScoreTool,
  factorCatalogTool,
  factorEvaluateCandidateTool,
  factorProposeTool,
  factorListCandidatesTool,
  factorRunDiscoveryTool,
] as const;
