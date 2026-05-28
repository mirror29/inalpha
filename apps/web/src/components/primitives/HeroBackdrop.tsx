/**
 * Hero 背景 —— 右侧的决策日志流。
 *
 * 日志流贴 hero 右半部分上升漂浮，像角落里一块开着的 monitor。
 * 标题占据左 / 中视觉重心，日志在右侧不抢戏，呼应 "agent + audit" 主题。
 *
 * 三层：
 *   1. 慢速 pan 的 dot field 打底（70s · 中心向外渐隐）。
 *   2. 右侧 LogStream：18 行独立 delay/duration，从底部漂到顶部。
 *   3. 底部 → bg 渐隐，无缝衔接下方 BlackBoxProblem。
 *
 * 全部 CSS-only；prefers-reduced-motion 仅降速到 240s（保留内容可见性）。
 */

const LOG_LINES = [
  '{ts:"08:01:12.441Z", agent:"bull",  action:"propose",      symbol:"BTCUSDT", confidence:0.62}',
  '{ts:"08:01:09.118Z", agent:"risk",  action:"approve",      planId:"pln_7c91", ttl:60s}',
  '{ts:"08:01:06.902Z", hook:"audit-log", tool:"trade.execute_plan", isError:false}',
  '{ts:"08:01:03.774Z", agent:"bear",  action:"counter",      thesis:"vol regime shift"}',
  '{ts:"08:00:58.215Z", tool:"swarm.run_backtest_grid", workers:6,  runs:9,  wall_ms:4218}',
  '{ts:"08:00:54.030Z", tool:"data.get_bars",   venue:"binance", fresh:true,  bars:240}',
  '{ts:"08:00:49.667Z", hook:"grid-size-cap",  decision:"allow", grid:9, cap:20}',
  '{ts:"08:00:47.001Z", tool:"research.deep_dive", asOf:"2026-05-28T08:00Z", analysts:3}',
  '{ts:"08:00:42.318Z", tool:"trade.create_plan", intent:"open_long", qty:0.05, expireAt:+300s}',
  '{ts:"08:00:38.882Z", tool:"paper.run_backtest", strategy:"donchian(20)", sharpe:2.14}',
  '{ts:"08:00:34.501Z", hook:"strategy-code-audit", verdict:"pass", lints:0}',
  '{ts:"08:00:31.220Z", tool:"factor.compute", id:"f_mom_12_2", windowDays:12,  ic:0.18}',
  '{ts:"08:00:27.064Z", agent:"orchestrator", route:"swarm.run_backtest_grid"}',
  '{ts:"08:00:22.770Z", tool:"trade.approve_plan", approvalToken:"tk_[REDACTED]", oneShot:true}',
  '{ts:"08:00:18.143Z", tool:"data.backfill_bars", venue:"yfinance", symbol:"^GSPC", days:365}',
  '{ts:"08:00:14.301Z", hook:"inject-current-date", asOf:"2026-05-28", source:"runtime"}',
  '{ts:"08:00:09.998Z", tool:"paper.compose_strategy", base:"donchian", mutation:"window+5"}',
  '{ts:"08:00:05.221Z", agent:"orchestrator", maxSteps:15, step:7,  remaining:8}',
];

/**
 * 渲染参数。
 * - VISIBLE_LINES：同时上场的行数（小于 LOG_LINES.length，剩余作池子）
 * - CYCLE_S：base 周期；所有行共享同一个公倍数周期，spacing = CYCLE_S / N
 *   严格垂直等距 → 永不重叠
 * - DUR_JITTER：duration 围绕 base 做 ±jitter 的三档循环，避免节奏死板
 * - LEFT_BUCKETS：水平偏移按桶取，散开到 0-30% 让行不全从最左切入
 */
const VISIBLE_LINES = 12;
const CYCLE_S = 72;
const DUR_JITTER = [0, 6, -5] as const; // 三档：72 / 78 / 67
const LEFT_BUCKETS = [0, 22, 8, 28, 4, 18, 12, 26, 2, 20, 10, 30] as const;

/**
 * 行级排布：严格等距 delay + 三档 duration + 桶状 left。
 * 不用 Math.random，避免 SSR / 客户端 hydration mismatch。
 */
function lineProps(i: number) {
  const duration = CYCLE_S + DUR_JITTER[i % DUR_JITTER.length];
  // 关键：delay 严格等距 = -i × (CYCLE_S / N)，确保任意瞬间相邻两行垂直间距相同
  const delay = -(i * (CYCLE_S / VISIBLE_LINES));
  const left = LEFT_BUCKETS[i % LEFT_BUCKETS.length];
  return { left, duration, delay };
}

export function HeroBackdrop() {
  return (
    <div
      aria-hidden
      className="pointer-events-none absolute inset-0 overflow-hidden"
    >
      {/* Layer 1 · drifting dot grid（中心向外渐隐） */}
      <div className="absolute inset-0 dot-grid drift-dots opacity-40 [mask-image:radial-gradient(ellipse_at_center,black_35%,transparent_82%)]" />

      {/* Layer 2 · 右侧 LogStream（容器贴右半，避开标题占据的左 / 中区） */}
      <div
        className="absolute inset-y-0 right-0 w-[52%] overflow-hidden"
        style={{
          // 顶部 14% 透明避开 LocaleSwitcher (top-6 right-6) +
          // ticker strip 余量；右边 fade 让长行温柔切掉
          maskImage:
            "linear-gradient(to bottom, transparent, black 14%, black 92%, transparent)",
          WebkitMaskImage:
            "linear-gradient(to bottom, transparent, black 14%, black 92%, transparent)",
        }}
      >
        {/* 容器内右侧 fade，让长行边缘虚化（避免硬切观感） */}
        <div
          className="absolute inset-0"
          style={{
            maskImage:
              "linear-gradient(to right, transparent, black 8%, black 88%, transparent)",
            WebkitMaskImage:
              "linear-gradient(to right, transparent, black 8%, black 88%, transparent)",
          }}
        >
          {Array.from({ length: VISIBLE_LINES }).map((_, i) => {
            // 从池子里循环取行，让 18 条文案 12 行轮换
            const line = LOG_LINES[i % LOG_LINES.length];
            const { left, duration, delay } = lineProps(i);
            return (
              <span
                key={i}
                className="log-rise absolute whitespace-nowrap font-mono text-[11.5px] font-light tracking-tight text-fg-muted/30"
                style={{
                  left: `${left}%`,
                  right: 0,
                  animationDuration: `${duration}s`,
                  animationDelay: `${delay}s`,
                }}
              >
                {line}
              </span>
            );
          })}
        </div>
      </div>

      {/* Bottom-edge fade —— 跟下方 BlackBoxProblem 无缝衔接 */}
      <div className="absolute inset-x-0 bottom-0 h-32 bg-gradient-to-b from-transparent to-bg" />
    </div>
  );
}
