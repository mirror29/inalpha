/**
 * services/data 的 Mastra tool 包装。
 *
 * Tool 设计遵循 [docs/05-tool-skill-discipline.md](../../../../docs/05-tool-skill-discipline.md)：
 * description 写"做什么 / 何时用 / 何时不用 / 坑"四要素。
 */
import { createTool } from "@mastra/core/tools";
import { z } from "zod";

import { defaultServiceSubject, mintServiceToken } from "../auth.js";
import { DataClient } from "../clients/data.js";
import { getSettings } from "../config.js";

// D-9 multi-market：扩 timeframe 到 5 种 venue 的并集（不支持的 timeframe 由后端 422 拒绝）。
const TimeframeSchema = z.enum([
  "1m", "5m", "15m", "30m", "1h", "4h",
  "1d", "1wk", "1mo", "1q", "1y",
]);

// D-9 multi-market：放宽到支持 5 种 venue 的 symbol 格式：
//   - crypto:      'BTC/USDT' (CCXT)
//   - 美股 / FRED:  'AAPL' / 'DFF' (plain alphanumeric)
//   - 指数:         '^N225' / '^FTSE' (Yahoo 指数前缀)
//   - akshare:     'sh.600519' / 'hk.00700' / 'jp.6758' (prefix.code)
//   - yfinance:    '005930.KS' / 'BHP.AX' / 'BARC.L' (code.suffix)
// Python 后端再做精细校验；zod 只做"非空 + 字符集 + 长度"防呆。
const SymbolSchema = z
  .string()
  .min(1)
  .max(50)
  .regex(
    /^[\^A-Za-z0-9._/\-:]+$/,
    "symbol 不能为空 / 含空格；支持 crypto 'BTC/USDT' / 普通 'AAPL' / 指数 '^N225' / akshare 'sh.600519' / yfinance '005930.KS' / FRED 'DFF'",
  );

type ToolRequestContext = {
  /** forward 用户 JWT 给 data-service；缺省时 fallback 自签 service token（dev 友好）。 */
  authToken?: string;
};

async function getClient(ctx?: ToolRequestContext): Promise<DataClient> {
  const settings = getSettings();
  const token = ctx?.authToken ?? (await mintServiceToken({ sub: defaultServiceSubject() }));
  return new DataClient({ baseUrl: settings.dataServiceUrl, token });
}

/** backfill 专用长超时 client —— CCXT rate-limited fetch_ohlcv，大跨度可能分钟级 */
async function getBackfillClient(ctx?: ToolRequestContext): Promise<DataClient> {
  const settings = getSettings();
  const token = ctx?.authToken ?? (await mintServiceToken({ sub: defaultServiceSubject() }));
  return new DataClient({
    baseUrl: settings.dataServiceUrl,
    token,
    // 5 分钟：1 年 1m ≈ 2-3 分钟，1 个月 1m ≈ 10-20 秒；给到 300s 留余量
    timeoutMs: 300_000,
  });
}

// ────────────────────────────────────────────────────────────────────
// data.get_bars
// ────────────────────────────────────────────────────────────────────

export const dataGetBarsTool = createTool({
  id: "data.get_bars",
  description: `
    取 K 线 OHLCV，支持 1m/5m/15m/1h/4h/1d 等。返回按时间正序 (ts ASC) 的 bar 列表。
    **数组最后一根**是时间窗口内最新一根；limit 截断时取最新 N 根。

    fresh 参数（D-9 加，关键）：
    - fresh=true：**先调 backfill 拉最近窗口的实时 K 线再读 DB**——拿到的就是"现在最新"
    - fresh=false（默认）：**只读 DB 缓存**——可能 stale 几小时到几天

    意图与 fresh 选择：
    - 意图"看最近 / 最新 / 当前的 K 线"（用户想要实时） → **fresh=true**
    - 意图"看历史走势 / 做技术分析 / 跑 backtest 前确认数据" → fresh=false 即可
    - 意图"现价单值不要 K 线" → 不要用这个，用 data.get_ticker
    - 意图"补拉一段历史时段还没缓存的数据" → 用 data.backfill_bars

    fresh=true 的开销：内部多走一次 backfill（CCXT 网络调用，limit 根 1m ≈ 1 秒），
    比纯 DB 慢约 1-3 秒；可接受。
  `.trim(),

  inputSchema: z.object({
    venue: z.string().default("binance"),
    symbol: SymbolSchema,
    timeframe: TimeframeSchema.default("1h"),
    fromTs: z
      .string()
      .datetime()
      .optional()
      .describe("ISO 8601 起始；省略默认 = 当前 - 1 年"),
    toTs: z
      .string()
      .datetime()
      .optional()
      .describe("ISO 8601 结束；省略默认 = 当前"),
    // 默认 500（旧默认 10_000 实测是 token 炸弹：1 万根 ≈ 200k+ token 直接进
    // 消息历史）。LLM 分析几百根足够；max 保持 50_000 兼容 scheduler 存量 job，
    // 但响应会被截到最新 BARS_RESPONSE_CAP 根（带 bars_truncated_from 标记）。
    limit: z.number().int().min(1).max(50_000).default(500),
    fresh: z
      .boolean()
      .default(false)
      .describe(
        "true 时先 backfill 最近窗口再读 DB,拿到真·实时 K 线;false 只读 DB 缓存。" +
          "意图是'最近/最新/当前 N 根'时务必传 true。",
      ),
  }),
  execute: async (inputData, ctx) => {
    const tc = ctx?.requestContext as ToolRequestContext | undefined;
    const venue = inputData.venue ?? "binance";
    const symbol = inputData.symbol;
    const timeframe = inputData.timeframe ?? "1h";
    const limit = inputData.limit ?? 500;
    const now = new Date();
    const fromTs = inputData.fromTs ?? new Date(now.getTime() - 365 * 86_400_000).toISOString();
    const toTs = inputData.toTs ?? now.toISOString();

    // fresh=true：先 backfill 最近 "limit × timeframe × 2" 的窗口（2 倍保险），
    // 然后再走 DB 读 —— 保证拿到的是刚补进来的最新 bar。
    if (inputData.fresh === true) {
      const tfSeconds = timeframeToSeconds(timeframe);
      // 上限 7 天回看：避免 LLM 传巨大 limit 时一口气 backfill 一年
      const lookbackMs = Math.min(tfSeconds * 1000 * limit * 2, 7 * 86_400_000);
      const freshFromTs = new Date(now.getTime() - lookbackMs).toISOString();
      const backfillClient = await getBackfillClient(tc);
      try {
        await backfillClient.backfillBars({
          venue,
          symbol,
          timeframe,
          fromTs: freshFromTs,
          toTs: now.toISOString(),
        });
      } catch (err) {
        // 不让 backfill 失败阻断查询 —— 让 caller 看 stale 数据 + 错误提示
        const message = err instanceof Error ? err.message : String(err);
        const client = await getClient(tc);
        const bars = await client.getBars({
          venue,
          symbol,
          timeframe,
          fromTs,
          toTs,
          limit,
        });
        return {
          ...capBars(bars),
          fresh_backfill_failed: true,
          fresh_backfill_error: message,
        };
      }
    }

    const client = await getClient(tc);
    const bars = await client.getBars({ venue, symbol, timeframe, fromTs, toTs, limit });
    return capBars(bars);
  },
});

/** 进 LLM context 的 bars 上限；超出截到**最新** N 根（旧的被丢，K 线最新根才是判读重点）。 */
const BARS_RESPONSE_CAP = 1_000;

/**
 * bars 响应护栏（防上下文爆炸，同 run_backtest equity_curve 降采样动机）：
 * 上限内原样返回；超限截最新 N 根并带 ``bars_truncated_from`` 标原始根数。
 */
function capBars(bars: unknown[]): Record<string, unknown> {
  if (bars.length <= BARS_RESPONSE_CAP) {
    return { bars, count: bars.length };
  }
  return {
    bars: bars.slice(-BARS_RESPONSE_CAP),
    count: BARS_RESPONSE_CAP,
    bars_truncated_from: bars.length,
  };
}

/** timeframe -> 秒；fresh 路径估算回看窗口用。未识别 fallback 1h。 */
function timeframeToSeconds(tf: string): number {
  const m: Record<string, number> = {
    "1m": 60, "5m": 300, "15m": 900, "30m": 1800,
    "1h": 3600, "4h": 14400,
    "1d": 86400, "1wk": 7 * 86400, "1mo": 30 * 86400,
    "1q": 90 * 86400, "1y": 365 * 86400,
  };
  return m[tf] ?? 3600;
}

// ────────────────────────────────────────────────────────────────────
// data.backfill_bars
// ────────────────────────────────────────────────────────────────────

export const dataBackfillBarsTool = createTool({
  id: "data.backfill_bars",
  description: `
    从外部市场拉历史 K 线落到 TimescaleDB。幂等（ON CONFLICT DO UPDATE）。

    何时用：
    - 准备跑 backtest 但数据库还没该时段的 K 线
    - 用户问"这个时段还没数据" → 先 backfill
    - **想要"最新现价"但 data.get_bars 返回的是 stale 数据**（数小时前）→ backfill 最近 1 天
    - **venue 按 symbol 所属市场自动选**（详 orchestrator system prompt 市场→venue 路由表）：
      crypto→binance、美股/全球指数/韩澳印加巴法等单股→yfinance、A/港/日/英/德股→akshare、
      美股专业 feed→alpaca、FRED 宏观→fred

    何时不用：
    - 已有数据 → 直接 data.get_bars
    - 实时订阅 → 走 WS（D-9+）

    ⚠️  **数据量 × rate limit 速查表**（决定 from→to 跨度时参考）：
    -    1d / 1d  ≈  1 根     → 即时返回
    -   1m / 1h   ≈ 60 根     → ~1 秒
    -   1d / 1m   ≈ 1440 根   → ~2 秒
    -   1w / 1m   ≈ 10080 根  → ~10 秒
    -   1M / 1m   ≈ 43200 根  → ~40 秒
    -  **1y / 1m  ≈ 525600 根 → 2-3 分钟（接近 tool 5 分钟超时上限）**

    坑：
    - **不要"为了保险拉一年 1m"**——既慢又没必要。需要近期价用 1h timeframe + 1 周即可
    - **akshare 仅日级**（1d/1wk/1mo）；**fred 仅日级及以上**（1d/1wk/1mo/1q/1y）；
      venue/timeframe 不匹配后端返 422 并带 supported_timeframes，按响应改一次再调，不要瞎重试
    - 不要在 LLM 单 turn 里循环 backfill 多个标的，分多次 tool call
    - tool 超时上限 5 分钟；超过这个跨度的 backfill 拆成多次小窗口调用
  `.trim(),
  inputSchema: z.object({
    venue: z.string().default("binance"),
    symbol: SymbolSchema,
    timeframe: TimeframeSchema.default("1h"),
    fromTs: z
      .string()
      .datetime()
      .optional()
      .describe("ISO 8601 起始；省略默认 = 当前 - 1 年"),
    toTs: z.string().datetime().optional().describe("ISO 8601 结束；省略默认 = 当前"),
  }),
  execute: async (inputData, ctx) => {
    const tc = ctx?.requestContext as ToolRequestContext | undefined;
    // backfill 专用长 timeout client（5 分钟）—— 默认 30s 对 CCXT 大跨度 fetch_ohlcv 不够
    const client = await getBackfillClient(tc);

    const now = new Date();
    const fromTs = inputData.fromTs ?? new Date(now.getTime() - 365 * 86_400_000).toISOString();
    const toTs = inputData.toTs ?? now.toISOString();

    return await client.backfillBars({
      venue: inputData.venue ?? "binance",
      symbol: inputData.symbol,
      timeframe: inputData.timeframe ?? "1h",
      fromTs,
      toTs,
    });
  },
});

// ────────────────────────────────────────────────────────────────────
// data.get_ticker —— 实时最新价（D-9 加；解决 get_bars 拿到 stale 缓存问题）
// ────────────────────────────────────────────────────────────────────

export const dataGetTickerTool = createTool({
  id: "data.get_ticker",
  description: `
    取 venue/symbol 的"最新价"（单值，不是 K 线）。

    - fresh=true（**默认**）：直接调外部市场实时报价，绕过 DB 缓存。
      **支持 venue**：binance / yfinance / alpaca；akshare / fred 不支持（返
      FRESH_NOT_SUPPORTED_FOR_VENUE，hint 提示切 fresh=false）。
      网络抖动 ~200-800ms；不要高频循环调（rate-limit）。
    - fresh=false：从 DB 拿最新 1m → fallback 1h，任意 venue 都支持。
      免网络但可能 stale 几分钟到几小时；返回字段 \`is_stale\` 标记是否超过 5 分钟。

    何时用：
    - 用户问 "X 现在多少 / 现价 / 实时价 / 最新价" → fresh=true（默认）；
      X 是 crypto / 美股 / 全球指数都行，按市场分类选 venue（同 backfill_bars 路由表）
    - scheduler 定时任务里要拿真·实时价 → mode='tool', tool='data.get_ticker', input={venue, symbol, fresh:true}
    - 撮合下单前要 refPrice → 不要在 LLM 侧调；paper /orders/submit 服务端自取

    何时不用：
    - 要历史走势 / 做技术分析 → 用 data.get_bars
    - 要 N 根 K 线 → 用 data.get_bars
    - A 股 / 港股 / FRED 想要"实时"价 → akshare/fred 没这能力，改 fresh=false 走 DB
      cache（先 backfill_bars 灌一遍最新数据再调）

    坑：
    - 默认 fresh=true！想要 DB cache 必须显式传 fresh:false
    - 返回 price 是单 float，没有 OHLCV 详情
    - 不是 K 线时间戳；ts 是外部市场那一刻的报价时间（yfinance 兜底用本地 now，
      非交易时段返上一交易日收盘价 + is_stale=true）
  `.trim(),
  inputSchema: z.object({
    venue: z.string().default("binance"),
    symbol: SymbolSchema,
    fresh: z.boolean().default(true).describe("true 直接调交易所；false 走 DB cache"),
  }),
  execute: async (inputData, ctx) => {
    const tc = ctx?.requestContext as ToolRequestContext | undefined;
    const client = await getClient(tc);
    return await client.getTicker({
      venue: inputData.venue ?? "binance",
      symbol: inputData.symbol,
      fresh: inputData.fresh ?? true,
    });
  },
});

// ────────────────────────────────────────────────────────────────────
// data.get_fundamentals —— 财报基本面（D-10 新增）
// ────────────────────────────────────────────────────────────────────

export const dataGetFundamentalsTool = createTool({
  id: "data.get_fundamentals",
  description: `
    拉标的的财报基本面数据（市盈率、ROE、营收增速等）。
    支持 venue=akshare（A股/港股）和 venue=yfinance（美股/全球）。

    何时用：研究个股基本面 / 看估值是否合理 / deep_dive 前预拉数据
    何时不用：加密货币（无传统财报）/ 指数和宏观（走 FRED 或专用数据源）

    坑：
    - akshare 返回字段因市场不同有差异；缺失字段为 null 不抛错
    - asOf（point-in-time，ADR-0053）：研究历史某时点时传入，只返回那时已披露的财报，
      防未来函数（读到当时还没发布的报告期）；仅 akshare 生效，yfinance 不做 PIT、
      响应 reason 会标注。研究"当下"不必传。
    - A股（sh/sz）的 market_cap/pe_ratio/pb_ratio 来自**实时** Baidu 源、无历史回溯：传
      **历史 asOf** 时这些估值字段留空（只有 ROE/毛利率等财报指标做 PIT），避免把当日估值
      混进历史财报；asOf=今天/不传 时估值正常返回。
  `.trim(),
  inputSchema: z.object({
    venue: z.string().default("akshare"),
    symbol: SymbolSchema,
    asOf: z
      // offset:true 必须——akshare 响应回显 / Python isoformat 都用 +00:00，不带 offset
      // 的 .datetime() 只认 Z 结尾会拒 +00:00，导致 agent 把上轮 as_of 复制回来即失败
      // （与 factor.ts 同名字段一致，#102 CR）。
      .string()
      .datetime({ offset: true })
      .optional()
      .describe("point-in-time 截断（ISO 8601）；只返回该时点已披露的财报，防未来函数"),
  }),
  execute: async (inputData, ctx) => {
    const tc = ctx?.requestContext as ToolRequestContext | undefined;
    const client = await getClient(tc);
    return await client.getFundamentals({
      venue: inputData.venue ?? "akshare",
      symbol: inputData.symbol,
      asOf: inputData.asOf,
    });
  },
});

// ────────────────────────────────────────────────────────────────────
// data.search_symbol —— 公司名 / 关键词 → ticker 解析
// ────────────────────────────────────────────────────────────────────

export const dataSearchSymbolTool = createTool({
  id: "data.search_symbol",
  description: `
    按公司名 / 关键词 / 代码片段检索 ticker。A股走 akshare 全量代码表
    （返回 sh.600519 格式，直接可喂 get_bars / get_fundamentals），
    其他市场走 Yahoo 全球检索（返回 AAPL / 0700.HK 等 yahoo 格式，venue=yfinance）。

    何时用：
    - 从新闻 / 研究里拿到公司名，要落到行情 / 财报数据前先解析出 symbol
    - 用户报了个公司名但你不确定代码——**禁止凭训练记忆猜代码**，用本 tool 核
    - 建候选池时批量确认每家公司的可交易代码

    何时不用：
    - 用户已给出明确 symbol → 直接用
    - 加密货币对（BTC/USDT 这类）→ 不是股票检索范围

    坑：
    - **已能判断出市场时显式传 venue**（美股 / 港股 / 全球 → yfinance；A股 → akshare）。
      query 的语言 ≠ 市场：中文名问美股公司极常见，别让 auto 的兜底逻辑替你判断
    - venue=auto：中文 query 会 A股表与 Yahoo 并行都查、轮替合并；纯英文只走 Yahoo
    - 北交所代码暂不支持（会被过滤）；返回空 ≠ 公司不存在，可换关键词再试
    - 返回的 venue 字段标明该 symbol 该配哪个数据源，喂 get_fundamentals 时带上
  `.trim(),
  inputSchema: z.object({
    query: z.string().min(1).max(80).describe("公司名 / 代码片段，如公司中文名或英文名"),
    venue: z
      .enum(["auto", "akshare", "yfinance"])
      .default("auto")
      .describe("已判断出市场就显式传（语言≠市场）；auto 仅在市场未知时兜底"),
    maxResults: z.number().int().min(1).max(20).default(10),
  }),
  execute: async (inputData, ctx) => {
    const tc = ctx?.requestContext as ToolRequestContext | undefined;
    const client = await getClient(tc);
    return await client.searchSymbols({
      query: inputData.query,
      venue: inputData.venue ?? "auto",
      maxResults: inputData.maxResults ?? 10,
    });
  },
});

export const dataTools = [
  dataGetBarsTool,
  dataBackfillBarsTool,
  dataGetTickerTool,
  dataGetFundamentalsTool,
  dataSearchSymbolTool,
] as const;
