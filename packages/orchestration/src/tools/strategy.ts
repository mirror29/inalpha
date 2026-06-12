/**
 * D-9 · LLM 自创策略 tools（ADR-0020 E1 MVP）。
 *
 * 四个 tool 暴露给 orchestrator：
 *
 * - ``paper.author_strategy``：把 LLM 写的 Python 源码送进沙盒 → 落候选表 → 返回 candidate_id
 * - ``paper.list_candidates``：列候选（按 fitness 排序），用于"对比 / 找当前最优"
 * - ``paper.get_candidate``：取完整候选（含源码 / metrics / fitness），用于"看这个候选具体是啥"
 * - ``paper.promote_candidate``：把候选从 ``candidate`` 切到 ``promoted``；D-9.1b 起
 *   permission ``ask``（前端气泡确认）。promote 仅状态切换；要按行情自动跑模拟盘需
 *   再调 ``paper.start_strategy``（D-11 live runner 已实现）
 *
 * 跑回测复用 ``paper.run_backtest``，传 ``candidateId`` 走候选分支——本模块不开
 * 单独的"跑回测" tool，避免 LLM 误用。
 *
 * **审批门**（D-9.1b 起，ADR-0018 askUserChoice 接通）：promote_candidate 走
 * permission ``ask`` —— agent 调时前端会弹气泡让用户点 "允许 / 拒绝"。30 秒无响应
 * 自动 deny。LLM 仍须调前自检（fitness vs baseline + 等用户明确指令），避免让用户
 * 看到一个本不该出现的气泡。后端硬校验（``fitness IS NOT NULL`` + ``status='candidate'``）
 * 作为第二道防线。
 */
import { createTool } from "@mastra/core/tools";
import { z } from "zod";

import { defaultServiceSubject, mintServiceToken } from "../auth.js";
import { PaperClient } from "../clients/paper.js";
import { getSettings } from "../config.js";

type ToolRequestContext = { authToken?: string };

async function getClient(ctx?: ToolRequestContext): Promise<PaperClient> {
  const settings = getSettings();
  const token = ctx?.authToken ?? (await mintServiceToken({ sub: defaultServiceSubject() }));
  return new PaperClient({ baseUrl: settings.paperServiceUrl, token });
}

// ────────────────────────────────────────────────────────────────────
// paper.author_strategy
// ────────────────────────────────────────────────────────────────────

export const paperAuthorStrategyTool = createTool({
  id: "paper.author_strategy",
  description: `
    把你自己写的 Strategy 子类 Python 源码送进沙盒 → 落候选表 → 返回 candidate_id。
    **这是研究链路的默认出口**——绝大多数行情都该走这里，不是 compose_strategy。

    何时用（默认）：
    - 任何"针对当下行情设计策略"的需求——震荡 / 趋势 / 突破 / 反转 / 多因子 / 自定义信号
    - 用户描述包含具体逻辑细节（"RSI<30 才买"/"成交量翻倍才确认"/"5%回撤止损"）
    - paper.compose_strategy 返回 strategy_id=null（family 路由 reject）

    何时不用（少数）：
    - 用户**明确点名**内置策略（"用 sma_cross 跑一下 fast=5 slow=20"）→ 走 compose_strategy
    - 用户**明确**要看 buy_and_hold 基线本身的表现 → 走 compose_strategy
    - 自动 baseline 对照已经由 run_backtest(candidateId=...) 内置（同 buy_and_hold 并跑）——
      **不要**手动再调一次 compose+run_backtest 跑 buy_and_hold

    协议契约（写代码前必读，违反 → 沙盒拒绝你重写）：
    1. 必须 \`class XxxStrategy(Strategy): ...\`，且只有 1 个 Strategy 子类
    2. 必须覆写 \`on_bar(self, bar)\`（不覆写 = 不响应行情）
    3. \`__init__\` 签名必须接受 \`(self, name, clock, msgbus, instrument_id, timeframe='1h', ...你的策略参数=默认值)\`
       —— engine 注入前 5 个，**kwargs 也可
    4. **不要写任何 import**——以下符号已在 globals 注入，直接用：
       - Strategy / Bar / Order / OrderSide / OrderType / ClientOrderId / InstrumentId
       - OrderFilled / PositionOpened / PositionClosed / PositionChanged / OrderSubmitted / OrderAccepted / OrderRejected / OrderCanceled
       - deque / uuid4
    5. 允许 import 的 stdlib（白名单）：math / statistics / collections / dataclasses / typing / enum / json
    6. **禁止**：import os/sys/subprocess/socket/requests/urllib/...；eval/exec/compile/__import__；
       getattr/setattr/globals/locals；open()；dunder 访问（.__class__ / .__bases__ 等）；async/await
    7. \`on_start(self)\` 里调 \`self.subscribe_bars(self._instrument_id, self._timeframe)\` 订阅行情
    8. 下单：构造 \`Order(client_order_id=ClientOrderId('x-'+uuid4().hex[:8]), instrument_id=..., side=OrderSide.BUY/SELL, type=OrderType.MARKET, quantity=...)\`，
       然后 \`self.submit_order(order)\`

    **事件 / 数据字段速查**（写策略时**严格按这个清单**，不要凭印象猜——猜错运行时 AttributeError）：

    \`\`\`
    Bar（on_bar 入参）：
      instrument_id, timeframe, open, high, low, close, volume, ts_event, ts_init, data_epoch

    PositionOpened / PositionChanged / PositionClosed（继承 PositionEvent，字段完全相同）：
      instrument_id, strategy_id
      quantity         # float，当前持仓数量（带方向）
      avg_open_price   # float，加权平均开仓价 ⚠️ 不叫 avg_price ⚠️
      realized_pnl     # float，累计已实现盈亏
      generation       # int
      ts_event, ts_init

    OrderFilled（继承 OrderEvent）：
      client_order_id, strategy_id, ts_event, ts_init   # 基类字段
      venue_order_id, instrument_id, side               # 子类字段
      fill_quantity    # float ⚠️ 不叫 filled_quantity ⚠️
      fill_price       # float ⚠️ 不叫 avg_fill_price ⚠️
      trade_id, is_last_fill

    OrderSubmitted / OrderAccepted / OrderRejected / OrderCanceled：
      client_order_id, strategy_id, ts_event, ts_init   # 基类
      + venue_order_id (Accepted)；+ reason (Rejected/Canceled)

    Order（构造下单）：
      client_order_id, instrument_id, side, type, quantity
      + price (LIMIT 必填，MARKET 必须省略)

    OrderSide: OrderSide.BUY | OrderSide.SELL
    OrderType: OrderType.MARKET | OrderType.LIMIT | OrderType.STOP_MARKET | OrderType.STOP_LIMIT
    \`\`\`

    **Few-shot 模板**（sma_cross 简化版，照这个改写你的逻辑）：

    \`\`\`python
    class MyStrategy(Strategy):
        def __init__(
            self, name, clock, msgbus, instrument_id,
            timeframe="1h", fast_period=10, slow_period=30, trade_size=0.01,
        ):
            if fast_period >= slow_period:
                raise ValueError("fast_period must be < slow_period")
            super().__init__(name, clock, msgbus)
            self._instrument_id = instrument_id
            self._timeframe = timeframe
            self._fast = fast_period
            self._slow = slow_period
            self._trade_size = trade_size
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
                crossed_up = self._prev_fast <= self._prev_slow and fast > slow
                crossed_down = self._prev_fast >= self._prev_slow and fast < slow
                if crossed_up and not self._is_long:
                    self._submit(OrderSide.BUY)
                elif crossed_down and self._is_long:
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
    \`\`\`

    **非技术 thesis 的参数化范式**（D-12）：策略代码运行时只能看到 OHLCV，宏观 /
    基本面 / 估值结论要进策略，就把它**编码为静态参数**（risk_scale / position_pct /
    单向门），并在 description 写明依据 + 研究 as_of（"risk_scale=0.5：FRED 曲线倒挂
    + VIX 高位，risk-off，as_of 2026-XX-XX"）。regime 变了就 re-author 一版新参数。
    第二个模板演示"波动率目标仓位 × 宏观 risk_scale 油门"：

    \`\`\`python
    import statistics

    class VolTargetRegimeStrategy(Strategy):
        # risk_scale：宏观/基本面 regime 油门（risk-on 1.0 / 中性 0.7 / risk-off 0.4）
        # —— 取值依据写进 description，不要拍脑袋
        def __init__(
            self, name, clock, msgbus, instrument_id,
            timeframe="1h", sma_period=50, vol_period=20,
            target_vol=0.02, risk_scale=0.7, base_size=0.05,
        ):
            super().__init__(name, clock, msgbus)
            self._instrument_id = instrument_id
            self._timeframe = timeframe
            self._closes = deque(maxlen=max(sma_period, vol_period) + 1)
            self._sma_period = sma_period
            self._vol_period = vol_period
            self._target_vol = target_vol
            self._risk_scale = risk_scale
            self._base_size = base_size
            self._is_long = False
            self._held = 0.0

        def on_start(self):
            self.subscribe_bars(self._instrument_id, self._timeframe)

        def on_bar(self, bar):
            if bar.instrument_id != self._instrument_id:
                return
            self._closes.append(bar.close)
            if len(self._closes) <= self._sma_period:
                return
            closes = list(self._closes)
            sma = sum(closes[-self._sma_period:]) / self._sma_period
            rets = [closes[i] / closes[i - 1] - 1 for i in range(-self._vol_period, 0)]
            realized_vol = statistics.stdev(rets) or 1e-9
            # 波动率目标仓位 × 宏观油门：高波动自动缩仓，risk-off 再压一档
            size = self._base_size * min(2.0, self._target_vol / realized_vol) * self._risk_scale
            if bar.close > sma and not self._is_long:
                self._submit(OrderSide.BUY, size)
            elif bar.close < sma and self._is_long and self._held > 0:
                self._submit(OrderSide.SELL, self._held)  # 平仓卖实际持仓量，不是重算的 size

        def on_position_opened(self, event):
            self._is_long = event.quantity > 0
            self._held = event.quantity

        def on_position_changed(self, event):
            self._held = event.quantity

        def on_position_closed(self, event):
            self._is_long = False
            self._held = 0.0

        def _submit(self, side, qty):
            order = Order(
                client_order_id=ClientOrderId("x-" + uuid4().hex[:8]),
                instrument_id=self._instrument_id, side=side,
                type=OrderType.MARKET, quantity=qty,
            )
            self.submit_order(order)
    \`\`\`

    另一常用范式（不给完整代码）：**regime 单向门**——risk-off 时只允许减仓 / 禁止新开多
    （\`if not self._allow_new_longs: return\`），门的开关同样是静态参数 + description 写依据。

    返回字段：
    - candidate_id（UUID）——后续 paper.run_backtest({ candidateId }) 用
    - created（bool）——false 表示撞到现有同 hash 候选，返回老 ID（幂等，可直接复用）
    - audit ——审计摘要（通过路径里 ok=true）
    - warnings（D-12）——非阻断告警；**非空必须原样转告用户**。典型：factorContext
      里的因子 author 时 decay_state 已是 fading/decaying——策略依据在衰减，
      该因子不得作核心信号；坚持引用要降权并向用户说明理由

    失败模式：
    - 422 STRATEGY_AUDIT_FAILED：源码含禁止 import / 名字 / dunder 访问；按 findings 改
    - 422 STRATEGY_LOAD_FAILED：compile / exec 失败；语法或类体异常
    - 422 STRATEGY_CONTRACT_FAILED：协议不满足；按 message 改 __init__ 或加 on_bar

    坑：
    - **代码长度 ≤ 20KB**（hook 拦超长）
    - 不要写半成品策略——\`on_bar\` 一直 pass 会让回测 0 信号，浪费一次落库
    - 写完 author_strategy 立刻 \`paper.run_backtest({ candidateId })\` 拿 metrics + fitness
    - 回测响应自带 \`baseline\` 字段（buy_and_hold 对照）；判断 alpha 看 \`fitness > baseline.fitness\`

    因子血缘（ADR-0047，**研究链路驱动时必传 factorContext**）：
    - 若本策略来自 research.deep_dive / factor.timing 的研究产出，把当时的 top 因子
      （含 rank_ic / rank_ic_recent / decay_state）原样填进 factorContext——这是
      candidate 的因子血缘，promoted 上模拟盘后系统按它巡检"策略依据的因子衰减了没"
    - 策略逻辑应引用这些因子（或在 description 里说明为何取舍）；decay_state 已是
      decaying 的因子**不要**当核心信号——引用时要降权并说明理由
    - 纯用户手描逻辑、没查过因子的（"帮我写个 RSI<30 买"），不传 factorContext 即可，
      **不要编造**因子数值
  `.trim(),
  inputSchema: z.object({
    code: z
      .string()
      .min(20)
      .max(20_480)
      .describe("完整 Python 源码（1 个 Strategy 子类，零 inalpha import）"),
    description: z
      .string()
      .max(2000)
      .default("")
      .describe("策略逻辑 / 适用场景 / 关键参数的人话说明"),
    factorContext: z
      .object({
        venue: z.string().describe("因子快照对应的数据源（与研究/回测同口径）"),
        symbol: z.string().describe("标的"),
        timeframe: z.string().describe("周期"),
        asOf: z
          .string()
          .optional()
          .describe("快照时刻（ISO8601；factor.timing 响应里的 as_of）"),
        factors: z
          .array(
            z.object({
              id: z.string().describe("因子 id（factor.timing 返回的 name/id）"),
              rankIc: z.number().describe("快照时 rank_ic"),
              rankIcRecent: z
                .number()
                .optional()
                .describe("快照时 rank_ic_recent（近 1/3 窗 IC）"),
              direction: z
                .number()
                .optional()
                .describe("方向 +1/-1/0"),
              decayState: z
                .enum(["stable", "fading", "decaying"])
                .optional()
                .describe("快照时衰减态"),
            }),
          )
          .min(1)
          .max(20)
          .describe("策略设计依据的因子列表（来自 factor.timing / deep_dive factors）"),
      })
      .optional()
      .describe(
        "生成时因子血缘（ADR-0047）。研究链路驱动的策略必传；数值必须来自真实工具" +
          "返回，禁止编造。落 candidate.factor_snapshot，供 live runner 衰减巡检",
      ),
  }),
  execute: async (inputData, ctx) => {
    const tc = ctx?.requestContext as ToolRequestContext | undefined;
    const client = await getClient(tc);
    const fc = inputData.factorContext;
    return await client.authorStrategy({
      code: inputData.code,
      description: inputData.description ?? "",
      factorSnapshot: fc
        ? {
            venue: fc.venue,
            symbol: fc.symbol,
            timeframe: fc.timeframe,
            as_of: fc.asOf,
            factors: fc.factors.map((f) => ({
              id: f.id,
              rank_ic: f.rankIc,
              rank_ic_recent: f.rankIcRecent,
              direction: f.direction,
              decay_state: f.decayState,
            })),
            source: "author_tool",
          }
        : undefined,
    });
  },
});

// ────────────────────────────────────────────────────────────────────
// paper.list_candidates
// ────────────────────────────────────────────────────────────────────

export const paperListCandidatesTool = createTool({
  id: "paper.list_candidates",
  description: `
    列已落库的策略候选（按 fitness DESC NULLS LAST, created_at DESC）。

    何时用：
    - 用户问"我之前写过哪些策略 / 候选池里有什么"
    - 想对比"我刚写的这版跟历史最优差多少"
    - 准备 promote 之前先看 leaderboard

    何时不用：
    - 知道具体 candidate_id 想看完整源码 → paper.get_candidate
    - 跑回测 → paper.run_backtest({ candidateId })

    返回字段（不含完整源码省带宽）：
    - id / code_hash / description / author / status
    - metrics（最近一次回测 sharpe/calmar/drawdown/...） / fitness（多目标合成）
    - last_backtest_run_id（→ paper.list_backtest_runs 看完整 equity curve）

    坑：
    - fitness=null 表示该候选还没跑过回测
    - status='candidate'（默认）/ 'rejected' / 'promoted'；promoted 才能进 trade.create_plan
  `.trim(),
  inputSchema: z.object({
    status: z
      .enum(["candidate", "rejected", "promoted"])
      .optional()
      .describe("可选过滤状态"),
    authorId: z
      .string()
      .uuid()
      .optional()
      .describe("可选只看某用户创建的候选"),
    limit: z.number().int().min(1).max(200).default(50),
  }),
  execute: async (inputData, ctx) => {
    const tc = ctx?.requestContext as ToolRequestContext | undefined;
    const client = await getClient(tc);
    return await client.listCandidates({
      status: inputData.status,
      authorId: inputData.authorId,
      limit: inputData.limit ?? 50,
    });
  },
});

// ────────────────────────────────────────────────────────────────────
// paper.get_candidate
// ────────────────────────────────────────────────────────────────────

export const paperGetCandidateTool = createTool({
  id: "paper.get_candidate",
  description: `
    按 candidate_id 取候选完整内容（含完整源码 + 最近回测 metrics + fitness）。

    何时用：
    - 用户问"X 候选具体怎么写的 / 让我看看那段代码"
    - 想基于某个候选改一版（先 get 拿源码，自己改完再 author_strategy 入新候选）

    何时不用：
    - 只想 leaderboard / 排序 → paper.list_candidates
    - 跑回测 → paper.run_backtest({ candidateId })

    返回字段：
    - code（完整 Python 源码）
    - description / author / status / fitness / metrics / last_backtest_run_id
    - audit（创建时三道沙盒的审计摘要）

    坑：
    - 404 CANDIDATE_NOT_FOUND：UUID 不存在或拼写错
  `.trim(),
  inputSchema: z.object({
    candidateId: z.string().uuid().describe("候选 ID（UUID）"),
  }),
  execute: async (inputData, ctx) => {
    const tc = ctx?.requestContext as ToolRequestContext | undefined;
    const client = await getClient(tc);
    return await client.getCandidate(inputData.candidateId);
  },
});

// ────────────────────────────────────────────────────────────────────
// paper.promote_candidate
// ────────────────────────────────────────────────────────────────────

export const paperPromoteCandidateTool = createTool({
  id: "paper.promote_candidate",
  description: `
    把策略候选从 status='candidate' 切到 'promoted'（"草稿 → 正式"）。

    **审批门**（D-9.1b 起）：permission \`ask\` —— 调用时前端会弹气泡让用户点
    "允许 / 拒绝"。30 秒无响应 → 自动 deny。**所以你仍要满足三条硬性自检**（用户
    点允许后才能 promote，但浪费用户的点击是坏体验）：

      1. 查过候选：\`paper.get_candidate(candidateId)\` 或 \`list_candidates\` 拿到完整
         \`fitness\` / \`metrics\` / \`baseline\`，**亲眼看过数字**
      2. fitness 显著优于 baseline（\`fitness > baseline.fitness\`）且 max_drawdown_pct < 25%
      3. 用户在对话里**明确**说"上线 / promote / 转正 / 推到 trade 链路 / 把它发布"等指令；
         **不是**用户只是"看看 / 对比 / 评估"

    自检不齐就不要调——会让用户面对一个气泡确认本不该发生的操作；同时后端硬校验仍在
    （fitness IS NOT NULL + status='candidate'）。

    **调前必做**：在对话里给用户报告完整决策依据（候选 ID / fitness vs baseline /
    max_drawdown / 你打算 promote 的理由），然后**才**调 tool。这样用户点气泡时
    有充分信息可判断；事后看 audit log 也知道为啥 promote 了它。

    何时用：
    - 用户明确说"上线 / promote / 转正 / 推到 trade 链路 / 把它发布"等
    - 候选已跑过回测（\`fitness\` 非 null）且**显著优于 baseline**
    - 用户已经看过 metrics（sharpe / calmar / max_drawdown_pct）并确认想升级

    何时不用：
    - 用户只是问"这个怎么样 / 对比一下"——这是 list_candidates / get_candidate 的活
    - \`fitness\` 还是 null（没跑过回测）——后端会返 400 \`CANDIDATE_NOT_BACKTESTED\`，
      你应先调 \`paper.run_backtest({ candidateId })\` 拿 fitness
    - \`fitness\` 不及 baseline——你应主动建议"还没跑赢 buy and hold，建议重写一版策略"
      而不是 promote 这个失败品
    - 候选已经 promoted / rejected——后端返 409 \`CANDIDATE_NOT_PROMOTABLE\`

    **重要事实，必须明确告诉用户**：
    - promote 仅是状态切换，**不会自动开始交易**。**不要让用户以为 promote 完就在跑模拟盘**
    - promoted 后有两条路：(1) 走 \`trade.create_plan\` 手动下单；(2) 调 \`paper.start_strategy\`
      把它放到模拟盘**按行情自动跑 on_bar**（D-11 live runner **已实现**，不是 E2 待办）

    入参 \`reason\` 是审计字段，建议写明：
    - 回测区间 / 标的 / timeframe（"2026-Q2 BTC 1h"）
    - fitness 对比（"fitness=0.85 vs baseline=0.32"）
    - 关键风控指标（"max_drawdown=8% calmar=4"）

    返回字段（StrategyCandidateRecord 完整行）：
    - status='promoted'，\`audit.promotion = { reason, promoted_by, promoted_at }\`
    - 其它字段（code / fitness / metrics / last_backtest_run_id）原样回传

    失败模式：
    - 404 CANDIDATE_NOT_FOUND：UUID 不存在
    - 409 CANDIDATE_NOT_PROMOTABLE：已 promoted / rejected
    - 400 CANDIDATE_NOT_BACKTESTED：fitness=null，先 run_backtest
    - permission 被 ask 拦截：用户气泡里点拒绝 → 回报"已取消"，不要重试

    坑：
    - 不要 promote 完就替用户自动 start——start_strategy 是独立人工动作，promote 后
      要主动告诉用户"还需 start_strategy 才会真跑"
    - 不要 batch promote 多个候选——每次都会弹气泡，体验差；让用户挑一个最强的
  `.trim(),
  inputSchema: z.object({
    candidateId: z.string().uuid().describe("要 promote 的候选 ID（UUID）"),
    reason: z
      .string()
      .min(20)
      .max(1000)
      .describe(
        "为什么 promote：写明回测区间 / 标的 / fitness vs baseline / 风控指标，落审计 audit.promotion",
      ),
  }),
  execute: async (inputData, ctx) => {
    const tc = ctx?.requestContext as ToolRequestContext | undefined;
    const client = await getClient(tc);
    return await client.promoteCandidate(inputData.candidateId, inputData.reason);
  },
});

export const paperAuthoringTools = [
  paperAuthorStrategyTool,
  paperListCandidatesTool,
  paperGetCandidateTool,
  paperPromoteCandidateTool,
] as const;
