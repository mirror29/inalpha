/**
 * D-12 优化循环 真服务 e2e smoke test —— 跟 services/data + services/paper 联调。
 *
 * 用法（先起两个 service：`bash scripts/dev.sh` 或单独起 data:8001 + paper:8002）：
 *
 *   pnpm smoke:optloop
 *
 * 这个脚本不调 LLM，**直接调 tool 函数模拟 orchestrator 的优化诊断链路**，覆盖 D-12
 * B 线在真服务上跑通：
 *
 * 1. backfill 60d BTC/USDT 1h（给 holdout 切分留足够 bar）
 * 2. paper.author_strategy —— 落一个带参数的候选（factorContext 故意含 1 个 decaying
 *    因子）→ 验证 **B3 衰减前馈 warning** 在 author 时就返回
 * 3. paper.run_backtest({candidateId}) —— 验证 **B1 holdout validation 块**
 *    （train/holdout 各段 + decay_ratio）随响应返回
 * 4. paper.check_sensitivity({candidateId, params}) —— 验证 **B2 参数邻域敏感性**
 *    端点真服务跑通，返 verdict + 邻域分布
 * 5. paper.list_backtest_trades(run_id) —— 验证 **B4 逐笔成交 tool** 透传
 *
 * 末尾对每个 D-12 不变量做断言（缺失即 FAIL），让脚本能进 CI / 手动回归。
 */
import { existsSync } from "node:fs";
import { resolve } from "node:path";
import { loadEnvFile } from "node:process";

// 显式加载 .env（Node 24+ 自带 loadEnvFile）
const envPath = resolve(process.cwd(), ".env");
if (existsSync(envPath)) {
  loadEnvFile(envPath);
}

import { defaultServiceSubject, mintServiceToken } from "../src/auth.js";
import {
  dataBackfillBarsTool,
  paperAuthorStrategyTool,
  paperCheckSensitivityTool,
  paperListBacktestTradesTool,
  paperRunBacktestTool,
} from "../src/tools/index.js";

type AnyResult = Record<string, unknown>;

/** 带数值参数的候选策略：SMA cross + 静态 risk_scale（演示宏观 thesis 参数化）。
 *  数值参数（fast_period / slow_period）让 check_sensitivity 有东西可扰动。 */
const CANDIDATE_CODE = `
class SmokeOptStrategy(Strategy):
    def __init__(
        self, name, clock, msgbus, instrument_id,
        timeframe="1h", fast_period=5, slow_period=20, trade_size=0.01, risk_scale=0.7,
    ):
        if fast_period >= slow_period:
            raise ValueError("fast_period must be < slow_period")
        super().__init__(name, clock, msgbus)
        self._instrument_id = instrument_id
        self._timeframe = timeframe
        self._fast = fast_period
        self._slow = slow_period
        self._trade_size = trade_size * risk_scale
        self._closes = deque(maxlen=slow_period)
        self._prev_fast = None
        self._prev_slow = None
        self._is_long = False

    def on_start(self):
        self.subscribe_bars(self._instrument_id, self._timeframe)

    def on_bar(self, bar):
        if bar.instrument_id != self._instrument_id:
            return
        self._closes.append(bar.close)
        if len(self._closes) < self._slow:
            return
        fast = sum(list(self._closes)[-self._fast:]) / self._fast
        slow = sum(self._closes) / self._slow
        if self._prev_fast is not None:
            up = self._prev_fast <= self._prev_slow and fast > slow
            down = self._prev_fast >= self._prev_slow and fast < slow
            if up and not self._is_long:
                self._submit(OrderSide.BUY)
            elif down and self._is_long:
                self._submit(OrderSide.SELL)
        self._prev_fast = fast
        self._prev_slow = slow

    def on_position_opened(self, event):
        self._is_long = event.quantity > 0

    def on_position_closed(self, event):
        self._is_long = False

    def _submit(self, side):
        order = Order(
            client_order_id=ClientOrderId("x-" + uuid4().hex[:8]),
            instrument_id=self._instrument_id, side=side,
            type=OrderType.MARKET, quantity=self._trade_size,
        )
        self.submit_order(order)
`.trim();

function assert(cond: boolean, msg: string): void {
  if (!cond) throw new Error(`断言失败：${msg}`);
  console.log(`  ✓ ${msg}`);
}

async function main(): Promise<void> {
  const token = await mintServiceToken({ sub: defaultServiceSubject() });
  const ctx = { requestContext: { authToken: token } } as never;

  const toTs = new Date();
  const fromTs = new Date(toTs.getTime() - 60 * 24 * 3600 * 1000);

  console.log("─── 1. backfill 60d BTC/USDT 1h（Binance 不可达时用 DB 缓存） ───");
  try {
    await dataBackfillBarsTool.execute!(
      {
        venue: "binance",
        symbol: "BTC/USDT",
        timeframe: "1h",
        fromTs: fromTs.toISOString(),
        toTs: toTs.toISOString(),
      } as never,
      ctx,
    );
    console.log("  backfill ok");
  } catch (err) {
    console.log(`  ⚠️  backfill 失败（${(err as Error).message}），继续用 DB 缓存`);
  }

  console.log("\n─── 2. author_strategy（factorContext 含 1 个 decaying 因子）───");
  const authored = (await paperAuthorStrategyTool.execute!(
    {
      code: CANDIDATE_CODE,
      description:
        "smoke 优化循环候选：SMA cross + risk_scale 静态油门。risk_scale=0.7 仅为 smoke 占位。",
      factorContext: {
        venue: "binance",
        symbol: "BTC/USDT",
        timeframe: "1h",
        asOf: toTs.toISOString(),
        factors: [
          { id: "ta.sma_cross", rankIc: 0.08, rankIcRecent: 0.06, direction: 1, decayState: "stable" },
          // 故意放一个衰减中的因子 → 必须触发 B3 author 前馈 warning
          { id: "ta.rsi_14", rankIc: 0.07, rankIcRecent: 0.01, direction: -1, decayState: "decaying" },
        ],
      },
    } as never,
    ctx,
  )) as AnyResult;
  console.log(JSON.stringify(authored, null, 2));
  const candidateId = authored.candidate_id as string;
  assert(typeof candidateId === "string" && candidateId.length > 0, "拿到 candidate_id");
  const warnings = authored.warnings as string[] | undefined;
  // warnings 字段整个缺失 = 服务端是不含 D-12 B3 改动的旧代码（smoke 测的是
  // 正在运行的服务，不是 worktree 源码）——给明确诊断而非裸断言失败。
  if (warnings === undefined) {
    throw new Error(
      "author 响应无 warnings 字段 —— 正在跑的 paper:8002 很可能是不含 D-12 改动的\n" +
        "  旧代码。smoke 测的是【正在运行的服务】，要验本分支的 B 线必须先用本分支\n" +
        "  代码重启 paper（例：pkill -f inalpha_paper 后在本 worktree `bash scripts/dev.sh`）。",
    );
  }
  assert(
    warnings.some((w) => w.includes("ta.rsi_14") && w.includes("decaying")),
    "B3：decaying 因子在 author 时就返回衰减告警",
  );

  console.log("\n─── 3. run_backtest（candidate 路径，验 holdout validation 块）───");
  const report = (await paperRunBacktestTool.execute!(
    {
      candidateId,
      symbol: "BTC/USDT",
      venue: "binance",
      timeframe: "1h",
      fromTs: fromTs.toISOString(),
      toTs: toTs.toISOString(),
      initialCash: 10_000,
      feeRate: 0.001,
    } as never,
    ctx,
  )) as AnyResult;
  console.log(JSON.stringify(report, null, 2));
  const runId = report.run_id as string | null;

  if (report.blew_up === true) {
    // 穿仓回测物理不可信，validation 仍会算但意义有限——只断言字段存在不断言数值
    console.log("  ⚠️  本次回测 blew_up（撮合层守门触发），跳过 validation 数值断言");
  }
  const validation = report.validation as AnyResult | null | undefined;
  assert(validation != null, "B1：响应带 validation 块（默认 split=0.7）");
  if (validation) {
    const train = validation.train as AnyResult;
    const holdout = validation.holdout as AnyResult;
    assert(typeof train?.num_bars === "number", "B1：validation.train.num_bars 存在");
    assert(typeof holdout?.num_bars === "number", "B1：validation.holdout.num_bars 存在");
    console.log(
      `  → train.num_bars=${train.num_bars} holdout.num_bars=${holdout.num_bars} ` +
        `decay_ratio=${validation.decay_ratio} flags=${JSON.stringify(validation.flags)}`,
    );
  }

  console.log("\n─── 4. check_sensitivity（参数邻域 ±20%，验 verdict）───");
  const sens = (await paperCheckSensitivityTool.execute!(
    {
      candidateId,
      // 传最终收敛的完整参数（源码默认值不在扰动范围）
      params: { fast_period: 5, slow_period: 20, trade_size: 0.01, risk_scale: 0.7 },
      symbol: "BTC/USDT",
      venue: "binance",
      timeframe: "1h",
      fromTs: fromTs.toISOString(),
      toTs: toTs.toISOString(),
    } as never,
    ctx,
  )) as AnyResult;
  console.log(JSON.stringify(sens, null, 2));
  assert(
    ["robust", "cliff", "insufficient"].includes(sens.verdict as string),
    `B2：check_sensitivity 返合法 verdict（实得 '${sens.verdict}'）`,
  );
  const neighbors = (sens.neighbors as unknown[]) ?? [];
  // fast/slow/risk_scale 三个数值参数 × 2 方向 = 最多 6 组邻域（trade_size 是 sizing 不扰动）
  assert(neighbors.length >= 2, `B2：生成了邻域组合（${neighbors.length} 组）`);

  console.log("\n─── 5. list_backtest_trades（逐笔成交 tool）───");
  if (runId) {
    const trades = (await paperListBacktestTradesTool.execute!(
      { runId, limit: 5 } as never,
      ctx,
    )) as unknown[];
    console.log(`  前 ${trades.length} 笔成交：`);
    console.log(JSON.stringify(trades, null, 2));
    assert(Array.isArray(trades), "B4：list_backtest_trades 返回数组");
  } else {
    console.log("  ⚠️  run_id 为 null（DB 未配 / 落库失败），跳过逐笔成交断言");
  }

  console.log("\n─── ✅ D-12 优化循环 smoke test PASSED ───");
}

main().catch((err) => {
  console.error("\n✗ D-12 优化循环 smoke test FAILED:");
  console.error(err);
  process.exit(1);
});
