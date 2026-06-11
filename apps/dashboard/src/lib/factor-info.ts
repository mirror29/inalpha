/**
 * 因子说明字典 —— 按 factor_id 给每个因子一段"度量什么 + 怎么读"的人话解释。
 *
 * 放前端而非 factor 服务:说明要跟 dashboard 的 zh/en locale 走(后端 catalog 的
 * name 单语,无法 i18n);factor_id 含 "."(如 pandas_ta.rsi_14),没法直接做
 * next-intl 的 message key,故用 TS 模块。新因子缺说明时面板回退到通用文案,
 * 不会报错 —— 但加 adapter 时记得补这里。
 */

export interface FactorInfo {
  zh: string;
  en: string;
}

export const FACTOR_INFO: Record<string, FactorInfo> = {
  // ── pandas-ta 技术指标 ──
  "pandas_ta.rsi_14": {
    zh: "14 期相对强弱指数（0-100）：近期涨幅占总涨跌幅的比例。高位≈超买、低位≈超卖，经典均值回归信号——方向先验为负（读数越高，未来收益预期越低）。",
    en: "14-period Relative Strength Index (0-100): share of recent gains in total movement. High = overbought, low = oversold — a classic mean-reversion signal with a negative direction prior (higher reading, lower expected forward return).",
  },
  "pandas_ta.macd_hist": {
    zh: "MACD 柱（12/26 EMA 差减 9 期信号线），再除以收盘价做跨标的归一。柱为正且放大≈多头动能增强，转负≈动能衰竭。",
    en: "MACD histogram (12/26 EMA spread minus its 9-period signal line), divided by close for cross-instrument comparability. Positive and widening = strengthening bullish momentum; flipping negative = momentum fading.",
  },
  "pandas_ta.atr_pct_14": {
    zh: "14 期平均真实波幅（ATR）除以收盘价：纯波动率度量，本身不含方向，常用来定仓位大小和止损宽度。",
    en: "14-period Average True Range divided by close: a pure volatility measure with no directional content, commonly used for position sizing and stop width.",
  },
  "pandas_ta.bb_pctb_20": {
    zh: "布林带 %B：价格在 20 期均值 ±2σ 通道中的相对位置。>1 突破上轨、<0 跌破下轨；高位回落的均值回归先验。",
    en: "Bollinger %B: price position within the 20-period mean ±2σ band. >1 = above the upper band, <0 = below the lower band; mean-reversion prior at extremes.",
  },
  "pandas_ta.adx_14": {
    zh: "ADX 趋势强度（0-100）：只度量「趋势有多强」，不分多空。>25 常视为趋势市，低读数为震荡市——适合做趋势策略的开关而非方向信号。",
    en: "ADX trend strength (0-100): measures how strong the trend is, regardless of direction. >25 is commonly read as trending, low readings as ranging — better as a regime switch than a directional signal.",
  },
  "pandas_ta.stoch_k_14": {
    zh: "随机指标 %K：收盘价在近 14 期高低区间中的位置（0-100）。高位超买、低位超卖的均值回归先验。",
    en: "Stochastic %K: where the close sits within the last 14 periods' high-low range (0-100). Overbought high / oversold low, with a mean-reversion prior.",
  },
  "pandas_ta.roc_10": {
    zh: "10 期变动率：收盘价相对 10 根 bar 前的涨跌幅，最直接的短期动量度量。",
    en: "10-period Rate of Change: percentage move of close versus 10 bars ago — the most direct short-term momentum measure.",
  },
  "pandas_ta.sma_ratio_20_50": {
    zh: "SMA20/SMA50 − 1：快慢均线比值偏离。>0 为短均线在上（多头排列），可视作金叉/死叉的连续值版本。",
    en: "SMA20/SMA50 − 1: deviation of the fast/slow moving-average ratio. >0 means the fast MA is on top (bullish alignment) — a continuous version of the golden/death cross.",
  },
  "pandas_ta.cci_20": {
    zh: "顺势指标 CCI：典型价对 20 期均值的偏离除以平均绝对偏差。极端读数≈超买超卖，均值回归先验。",
    en: "Commodity Channel Index: typical price deviation from its 20-period mean, scaled by mean absolute deviation. Extreme readings = overbought/oversold, mean-reversion prior.",
  },
  "pandas_ta.mom_20": {
    zh: "20 期动量：收盘价相对 20 根 bar 前的收益率，中期动量度量。",
    en: "20-period momentum: return of close versus 20 bars ago — a medium-term momentum measure.",
  },
  "pandas_ta.vol_ratio_20": {
    zh: "量比：当前成交量除以 20 期均量。>1 为放量；需配合价格方向解读（放量上涨与放量下跌含义相反）。",
    en: "Volume ratio: current volume over its 20-period average. >1 = elevated volume; interpret together with price direction (volume surges on up vs down moves mean opposite things).",
  },
  "pandas_ta.obv_mom_20": {
    zh: "OBV（能量潮）20 期动量：按涨跌给成交量定正负后累计，再取近 20 期变化，度量资金净流入/流出的趋势。",
    en: "20-period momentum of On-Balance Volume: volume signed by price direction and accumulated, then differenced over 20 periods — gauges the trend of net money flowing in or out.",
  },
  "pandas_ta.willr_14": {
    zh: "Williams %R：收盘价距近 14 期高点的距离占区间比例（−100~0）。接近 0 ≈超买，接近 −100 ≈超卖。",
    en: "Williams %R: distance from close to the 14-period high as a share of the range (−100 to 0). Near 0 = overbought, near −100 = oversold.",
  },
  "pandas_ta.mfi_14": {
    zh: "资金流量指数：成交量加权的 RSI（0-100）。高位≈买方资金过热，低位≈卖压枯竭，均值回归先验。",
    en: "Money Flow Index: a volume-weighted RSI (0-100). High = buying pressure overheated, low = selling pressure exhausted; mean-reversion prior.",
  },
  "pandas_ta.cmf_20": {
    zh: "Chaikin 资金流：按收盘价在 bar 内位置加权的量能，20 期求和归一（−1~1）。>0 ≈资金净流入。",
    en: "Chaikin Money Flow: volume weighted by where the close sits within each bar, summed and normalized over 20 periods (−1 to 1). >0 = net inflow.",
  },

  // ── WorldQuant Alpha101（时序近似版,原版部分为横截面因子）──
  "alpha101.a101": {
    zh: "Alpha#101：(close−open)/(high−low)，单根 bar 实体占整根振幅的比例——日内动量的最简表达。",
    en: "Alpha#101: (close−open)/(high−low) — the candle body as a share of the full bar range; the simplest expression of intrabar momentum.",
  },
  "alpha101.a54": {
    zh: "Alpha#54 价位结构：low/close/open/high 的多项式组合，捕捉 bar 内价位排布隐含的反转信号。",
    en: "Alpha#54 price-structure: a polynomial combination of low/close/open/high that captures reversal signals implied by intrabar price arrangement.",
  },
  "alpha101.a23": {
    zh: "Alpha#23 高点反转：当 20 期高点均值高于当前 high 时，取 high 的 2 期差分的负值——冲高回落先验。",
    en: "Alpha#23 high reversal: when the 20-period mean of highs exceeds the current high, takes the negative 2-period difference of high — a fade-the-spike prior.",
  },
  "alpha101.a12": {
    zh: "Alpha#12 量驱动反转：sign(Δvolume) × (−Δclose)。放量当根反着做、缩量顺着做的短期反转因子。",
    en: "Alpha#12 volume-driven reversal: sign(Δvolume) × (−Δclose). Fades the move on rising volume, follows it on falling volume — a short-term reversal factor.",
  },
  "alpha101.a49": {
    zh: "Alpha#49 趋势/反转切换：按近期价格斜率是否超过阈值，在「跟趋势」与「做反转」之间切换的条件因子。",
    en: "Alpha#49 trend/reversal switch: a conditional factor that flips between trend-following and reversal depending on whether the recent price slope exceeds a threshold.",
  },
  "alpha101.a1": {
    zh: "Alpha#1：对近期收益的二阶矩做 argmax 后取 rank（原版为横截面 rank，这里为单标的时序近似）。捕捉波动结构里的动量。",
    en: "Alpha#1: rank of the argmax over second moments of recent returns (originally cross-sectional, approximated here as a single-instrument time series). Captures momentum embedded in the volatility structure.",
  },
  "alpha101.a3": {
    zh: "Alpha#3：open 与 volume 的负相关度（原版横截面 rank，这里时序近似）。量价背离度量。",
    en: "Alpha#3: negative correlation between open and volume (originally cross-sectional ranks, approximated as time series). A price-volume divergence measure.",
  },
  "alpha101.a6": {
    zh: "Alpha#6：−corr(open, volume, 10)。开盘价与成交量 10 期相关的负值，量价背离因子。",
    en: "Alpha#6: −corr(open, volume, 10). The negated 10-period correlation between open and volume — a price-volume divergence factor.",
  },

  // ── qlib Alpha158 风格 ──
  "qlib.kmid": {
    zh: "KMID：(close−open)/open，单根 bar 的实体涨跌幅。正≈阳线动量。",
    en: "KMID: (close−open)/open — the candle body return of a single bar. Positive = bullish bar momentum.",
  },
  "qlib.klen": {
    zh: "KLEN：(high−low)/open，单根 bar 的总振幅，波动率型因子。",
    en: "KLEN: (high−low)/open — the full range of a single bar; a volatility-type factor.",
  },
  "qlib.kup": {
    zh: "KUP 上影线占比：(high−max(open,close))/open。冲高没守住的程度，常作回落反转先验。",
    en: "KUP upper-shadow ratio: (high−max(open,close))/open. How much of the spike wasn't held — often a fade/reversal prior.",
  },
  "qlib.klow": {
    zh: "KLOW 下影线占比：(min(open,close)−low)/open。下探被买回的程度，多头承接力度。",
    en: "KLOW lower-shadow ratio: (min(open,close)−low)/open. How much of the dip got bought back — a measure of bid support.",
  },
  "qlib.roc_5": {
    zh: "ROC(5)：close 相对 5 根 bar 前的比值，超短动量。",
    en: "ROC(5): close relative to 5 bars ago — very short-term momentum.",
  },
  "qlib.roc_20": {
    zh: "ROC(20)：close 相对 20 根 bar 前的比值，中期动量。",
    en: "ROC(20): close relative to 20 bars ago — medium-term momentum.",
  },
  "qlib.roc_60": {
    zh: "ROC(60)：close 相对 60 根 bar 前的比值，长周期动量/趋势。",
    en: "ROC(60): close relative to 60 bars ago — long-horizon momentum/trend.",
  },
  "qlib.std_5": {
    zh: "STD(5)/close：近 5 期收盘价标准差除以现价，超短滚动波动率。",
    en: "STD(5)/close: 5-period standard deviation of close over the current price — very short rolling volatility.",
  },
  "qlib.std_20": {
    zh: "STD(20)/close：近 20 期收盘价标准差除以现价，中期滚动波动率。",
    en: "STD(20)/close: 20-period standard deviation of close over the current price — medium-term rolling volatility.",
  },
  "qlib.std_60": {
    zh: "STD(60)/close：近 60 期收盘价标准差除以现价，长周期波动率水平。",
    en: "STD(60)/close: 60-period standard deviation of close over the current price — long-horizon volatility level.",
  },
  "qlib.beta_20": {
    zh: "BETA(20)：收盘价对时间回归的斜率除以 close——近 20 期价格走势的「斜率」，趋势方向与强度。",
    en: "BETA(20): slope of close regressed on time, divided by close — the 'slope' of price over the last 20 periods; trend direction and strength.",
  },
  "qlib.beta_60": {
    zh: "BETA(60)：同 BETA(20) 但用 60 期窗口，更慢更稳的趋势斜率。",
    en: "BETA(60): same as BETA(20) over a 60-period window — a slower, steadier trend slope.",
  },
  "qlib.rsqr_20": {
    zh: "RSQR(20)：近 20 期价格对时间线性拟合的 R²。越接近 1，趋势越「干净」（直线性强）；低值为震荡。",
    en: "RSQR(20): R² of a linear fit of price on time over 20 periods. Closer to 1 = a 'cleaner', more linear trend; low values = choppy ranging.",
  },
  "qlib.max_20": {
    zh: "MAX(20)/close：近 20 期最高价除以现价，距前高的空间。大≈深度回撤后（可能反弹也可能弱势），均值回归先验。",
    en: "MAX(20)/close: 20-period high over the current price — headroom to the recent high. Large = deep drawdown (bounce or weakness); mean-reversion prior.",
  },
  "qlib.max_60": {
    zh: "MAX(60)/close：近 60 期最高价除以现价，长窗口的距前高空间。",
    en: "MAX(60)/close: 60-period high over the current price — distance to the high over a long window.",
  },
  "qlib.min_20": {
    zh: "MIN(20)/close：近 20 期最低价除以现价，距前低的空间。",
    en: "MIN(20)/close: 20-period low over the current price — cushion above the recent low.",
  },
  "qlib.min_60": {
    zh: "MIN(60)/close：近 60 期最低价除以现价，长窗口的距前低空间。",
    en: "MIN(60)/close: 60-period low over the current price — cushion above the low over a long window.",
  },
  "qlib.qtlu_20": {
    zh: "QTLU(20)：近 20 期收盘价 80 分位除以现价——价格相对近期分布上侧的位置。",
    en: "QTLU(20): the 80th percentile of close over 20 periods, divided by the current price — where price sits versus the upper side of its recent distribution.",
  },
  "qlib.qtld_20": {
    zh: "QTLD(20)：近 20 期收盘价 20 分位除以现价——价格相对近期分布下侧的位置。",
    en: "QTLD(20): the 20th percentile of close over 20 periods, divided by the current price — where price sits versus the lower side of its recent distribution.",
  },
  "qlib.rsv_5": {
    zh: "RSV(5)：close 在近 5 期高低区间中的位置（0-1），随机指标的原始值，超短超买超卖。",
    en: "RSV(5): where close sits in the 5-period high-low range (0-1) — the raw stochastic value; very short-term overbought/oversold.",
  },
  "qlib.rsv_20": {
    zh: "RSV(20)：close 在近 20 期高低区间中的位置（0-1），中期区间位置。",
    en: "RSV(20): where close sits in the 20-period high-low range (0-1) — medium-term range position.",
  },
  "qlib.corr_20": {
    zh: "CORR(20)：close 与 log(volume) 的 20 期滚动相关，量价同向程度。正≈放量推动价格。",
    en: "CORR(20): 20-period rolling correlation between close and log(volume) — how aligned price and volume are. Positive = volume is pushing price.",
  },
  "qlib.corr_60": {
    zh: "CORR(60)：close 与 log(volume) 的 60 期滚动相关，长窗口量价配合度。",
    en: "CORR(60): 60-period rolling correlation between close and log(volume) — long-window price-volume alignment.",
  },
  "qlib.cntp_20": {
    zh: "CNTP(20)：近 20 根 bar 中上涨 bar 的占比，「胜率」型动量。",
    en: "CNTP(20): share of up bars in the last 20 — a 'win-rate' style momentum measure.",
  },
  "qlib.cntp_60": {
    zh: "CNTP(60)：近 60 根 bar 中上涨 bar 的占比，长窗口胜率动量。",
    en: "CNTP(60): share of up bars in the last 60 — win-rate momentum over a long window.",
  },
  "qlib.cntn_20": {
    zh: "CNTN(20)：近 20 根 bar 中下跌 bar 的占比，与 CNTP 互补的空头压力度量。",
    en: "CNTN(20): share of down bars in the last 20 — the bearish-pressure complement of CNTP.",
  },
  "qlib.sump_20": {
    zh: "SUMP(20)：涨幅和 ÷（涨幅和+跌幅和），RSI 的另一种写法（0-1）。>0.5 多头占优。",
    en: "SUMP(20): sum of gains over total movement (gains + losses) — an alternative formulation of RSI (0-1). >0.5 = bulls dominating.",
  },
  "qlib.sump_60": {
    zh: "SUMP(60)：同 SUMP(20) 但用 60 期窗口，慢速多空力量对比。",
    en: "SUMP(60): same as SUMP(20) over 60 periods — a slower bull/bear balance measure.",
  },
  "qlib.vma_20": {
    zh: "VMA(20)：volume 除以 20 期量均线，量比（放量/缩量）。",
    en: "VMA(20): volume over its 20-period average — a volume ratio (expansion/contraction).",
  },
  "qlib.vstd_20": {
    zh: "VSTD(20)：20 期成交量标准差除以量均线，成交量的变异系数——量能是否稳定。",
    en: "VSTD(20): 20-period standard deviation of volume over its mean — the coefficient of variation of volume; how steady activity is.",
  },
  // ── macro · FRED daily（ADR-0044 Phase 1，发布滞后 +1 天）──
  "macro.dff_chg_20": {
    zh: "联邦基金利率 20 日变化：货币政策松紧的边际方向（FRED DFF，滞后 1 天）。",
    en: "20-day change in the federal funds rate — the marginal direction of policy tightening (FRED DFF, 1-day lag).",
  },
  "macro.dgs10_level": {
    zh: "10Y 美债收益率水平：无风险利率锚（FRED DGS10，滞后 1 天）。",
    en: "10Y Treasury yield level — the risk-free rate anchor (FRED DGS10, 1-day lag).",
  },
  "macro.dgs10_chg_20": {
    zh: "10Y 美债收益率 20 日变化：利率动量，上行压估值。",
    en: "20-day change in the 10Y Treasury yield — rate momentum; rising yields compress valuations.",
  },
  "macro.curve_slope": {
    zh: "期限利差 10Y-2Y：收益率曲线斜率，倒挂（<0）为衰退前瞻信号。",
    en: "10Y-2Y term spread — yield-curve slope; inversion (<0) is a classic recession lead.",
  },
  "macro.curve_slope_chg_20": {
    zh: "期限利差 20 日变化：曲线陡峭化/平坦化的方向。",
    en: "20-day change in the term spread — direction of curve steepening/flattening.",
  },
  "macro.dollar_roc_20": {
    zh: "广义美元指数 20 日动量：美元走强压风险资产（FRED DTWEXBGS）。",
    en: "20-day momentum of the broad dollar index — a stronger dollar pressures risk assets (FRED DTWEXBGS).",
  },
  "macro.dollar_roc_60": {
    zh: "广义美元指数 60 日动量：慢速美元趋势。",
    en: "60-day momentum of the broad dollar index — the slower dollar trend.",
  },
  "macro.vix_level": {
    zh: "VIX 水平：期权隐含波动率，恐慌度读数（FRED VIXCLS）。",
    en: "VIX level — option-implied volatility; the fear gauge (FRED VIXCLS).",
  },
  "macro.vix_chg_20": {
    zh: "VIX 20 日变化：恐慌升温/降温的方向。",
    en: "20-day change in VIX — direction of rising/cooling fear.",
  },
  // ── macro · FRED monthly（ADR-0044 Phase 2，per-series 发布滞后 40-60 天，
  //    在 1d bar 上是 ~30 bar 一变的阶梯函数）──
  "macro.cpi_yoy": {
    zh: "CPI 同比：通胀水平（FRED CPIAUCSL，月度，发布滞后 ~45 天）。",
    en: "CPI year-over-year — the inflation level (FRED CPIAUCSL, monthly, ~45-day publication lag).",
  },
  "macro.cpi_mom": {
    zh: "CPI 环比：通胀的月度脉冲。",
    en: "CPI month-over-month — the monthly inflation impulse.",
  },
  "macro.cpi_yoy_chg_3": {
    zh: "CPI 同比 3 月动量：通胀在加速还是降温。",
    en: "3-month change in CPI YoY — whether inflation is accelerating or cooling.",
  },
  "macro.core_cpi_yoy": {
    zh: "核心 CPI 同比：剔除食品能源的底层通胀（FRED CPILFESL）。",
    en: "Core CPI year-over-year — underlying inflation ex food & energy (FRED CPILFESL).",
  },
  "macro.unrate_level": {
    zh: "失业率水平：劳动力市场松紧（FRED UNRATE，月度，滞后 ~40 天）。",
    en: "Unemployment rate level — labor-market slack (FRED UNRATE, monthly, ~40-day lag).",
  },
  "macro.unrate_chg_3": {
    zh: "失业率 3 月变动：Sahm 式恶化信号，快速上行预警衰退。",
    en: "3-month change in unemployment — a Sahm-style deterioration signal; rapid rises warn of recession.",
  },
  "macro.payems_chg_1": {
    zh: "非农就业月增（千人）：经济动能的月度读数（FRED PAYEMS）。",
    en: "Monthly nonfarm payrolls change (thousands) — the monthly read on economic momentum (FRED PAYEMS).",
  },
  "macro.m2_yoy": {
    zh: "M2 同比增速：流动性扩张/收缩（FRED M2SL，月度，发布滞后 ~60 天）。",
    en: "M2 year-over-year growth — liquidity expansion/contraction (FRED M2SL, monthly, ~60-day lag).",
  },
};

/** 取因子说明,缺失返回 null(面板回退通用文案)。 */
export function factorDescription(
  factorId: string,
  locale: string,
): string | null {
  const info = FACTOR_INFO[factorId];
  if (!info) return null;
  return locale.startsWith("zh") ? info.zh : info.en;
}
