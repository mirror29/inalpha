"""策略原型库（archetype library）—— 给 agent 写策略当**起点骨架**（ADR-0051 D1）。

每个原型导出一段**完整可跑的候选源码**（``code``）+ 元数据（``ArchetypeMeta``）。
agent 流程（orchestrator 第 2 步）：

1. 支柱 A 先 ``factor.timing`` 拿当下 stable 因子 + 其 ``kind``
2. 调 ``paper.list_archetypes(factor_kinds=...)`` 按 kind 取匹配骨架
3. 以骨架 ``code`` 为起点，**按因子证据改参 / 改逻辑**，再走 ``paper.author_strategy``

**关键约束（骨架是起点不是绕过验证）**：

- ``code`` 是"候选源码"形态——**不能** ``from __future__`` / 相对 import（``ast_audit``
  会拒），inalpha 符号（Strategy / Bar / Order / OrderSide / OrderType / ClientOrderId /
  deque / uuid4 …）已由 ``dynamic_loader`` 注入 globals，直接用
- 每个 ``code`` 必须过 ``ast_audit`` 三审 + ``dynamic_loader.load_strategy_class`` +
  ``verify_strategy_contract``（``tests/test_archetypes.py`` 强制，骨架坏即 CI 红）
- 现货 **long-only**（与撮合层一致，不引入做空）；``__init__`` 接 ``position_pct`` /
  ``initial_cash`` 让 runner 自动注入仓位

**出处（MIT 借鉴，ADR-0051 §许可与出处）**：原型结构 + 失效模式 + 可转向目标种子自
[tradermonty/claude-trading-skills](https://github.com/tradermonty/claude-trading-skills)
（MIT License）``strategy-pivot-designer/references/strategy_archetypes.md``——**只提炼结构
原则，不复制其脚本源码**。``multi_factor_combine`` 为 Inalpha 因子库特有，无外部对应。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Final


@dataclass(frozen=True, slots=True)
class ArchetypeParam:
    """骨架的一个可调参数槽。``default`` 是**初值参考**，留给回测调，别钉死成魔法数。"""

    name: str
    default: float | int
    doc: str


@dataclass(frozen=True, slots=True)
class ArchetypeMeta:
    """单个策略原型的完整描述（含可跑源码 ``code``）。"""

    name: str
    #: 源 canonical（MIT 出处）；Inalpha 特有的为 ""
    source_archetype: str
    #: 适用的因子 kind（与 factor.timing 返回的 kind 对齐，做路由键）
    applies_to_kinds: tuple[str, ...]
    description: str
    when_to_use: str
    when_not_to_use: str
    #: 典型失效模式（借自源 canonical，喂自动 pivot 的风险判断）
    failure_modes: tuple[str, ...]
    #: 可转向的兼容原型名（喂 ADR-0051 D6 自动 pivot 的 archetype-switch）
    compatible_pivots: tuple[str, ...]
    params: tuple[ArchetypeParam, ...]
    #: 完整可跑候选源码（过沙盒三审）
    code: str


# ─────────────────────────────────────────────────────────────────────────────
# 1. momentum_trend ← trend_following_breakout
# ─────────────────────────────────────────────────────────────────────────────

_MOMENTUM_TREND_CODE = '''
class MomentumTrendStrategy(Strategy):
    # 趋势跟随：快慢均线金叉 + 量能确认入场，死叉离场。long-only 现货。
    def __init__(
        self, name, clock, msgbus, instrument_id, timeframe="1h",
        fast_period=10, slow_period=30, vol_mult=1.2, trade_size=0.01,
        position_pct=None, initial_cash=0.0,
    ):
        if fast_period >= slow_period:
            raise ValueError("fast_period must be < slow_period")
        super().__init__(name, clock, msgbus)
        self._instrument_id = instrument_id
        self._timeframe = timeframe
        self._fast = fast_period
        self._slow = slow_period
        self._vol_mult = vol_mult
        self._trade_size = trade_size
        self._position_pct = position_pct
        self._initial_cash = initial_cash
        self._closes = deque(maxlen=slow_period)
        self._vols = deque(maxlen=slow_period)
        self._prev_fast = None
        self._prev_slow = None
        self._is_long = False
        self._open_qty = 0.0

    def on_start(self):
        self.subscribe_bars(self._instrument_id, self._timeframe)

    def on_bar(self, bar):
        if bar.instrument_id != self._instrument_id:
            return
        if bar.timeframe != self._timeframe:
            return
        self._closes.append(bar.close)
        self._vols.append(bar.volume)
        if len(self._closes) < self._slow:
            return
        fast = sum(list(self._closes)[-self._fast:]) / self._fast
        slow = sum(self._closes) / self._slow
        avg_vol = sum(self._vols) / len(self._vols)
        vol_ok = bar.volume >= avg_vol * self._vol_mult
        if self._prev_fast is not None and self._prev_slow is not None:
            up = self._prev_fast <= self._prev_slow and fast > slow
            down = self._prev_fast >= self._prev_slow and fast < slow
            if up and vol_ok and not self._is_long:
                self._submit(OrderSide.BUY, bar)
            elif down and self._is_long:
                self._submit(OrderSide.SELL, bar)
        self._prev_fast = fast
        self._prev_slow = slow

    def on_position_opened(self, event):
        self._is_long = event.quantity > 0
        self._open_qty = abs(event.quantity)

    def on_position_closed(self, event):
        self._is_long = False
        self._open_qty = 0.0

    def _qty(self, bar):
        if (
            self._position_pct is not None and self._position_pct > 0
            and self._initial_cash > 0 and bar.close > 0
        ):
            return (self._initial_cash * self._position_pct) / bar.close / 1.05
        return self._trade_size

    def _submit(self, side, bar):
        if side == OrderSide.SELL and self._open_qty > 0:
            qty = self._open_qty
        else:
            qty = self._qty(bar)
        order = Order(
            client_order_id=ClientOrderId("mom-" + uuid4().hex[:8]),
            instrument_id=self._instrument_id,
            side=side,
            type=OrderType.MARKET,
            quantity=qty,
        )
        self.submit_order(order)
'''.strip()

_MOMENTUM_TREND = ArchetypeMeta(
    name="momentum_trend",
    source_archetype="trend_following_breakout",
    applies_to_kinds=("momentum", "trend"),
    description="快慢均线金叉 + 量能确认跟趋势，死叉离场；趋势在场持有。",
    when_to_use="主因子是 momentum / trend 且 direction=+1；行情有明确单边趋势时",
    when_not_to_use="震荡 / 区间市（金叉死叉反复 whipsaw）；主因子是 mean_reversion",
    failure_modes=(
        "震荡市反复 whipsaw 被手续费磨损",
        "追高晚入（趋势已走完才金叉）",
        "跳空跌穿止损位",
    ),
    compatible_pivots=("mean_reversion", "volatility_contraction", "multi_factor_combine"),
    params=(
        ArchetypeParam("fast_period", 10, "快线周期；按因子 horizon 调"),
        ArchetypeParam("slow_period", 30, "慢线周期；必须 > fast_period"),
        ArchetypeParam("vol_mult", 1.2, "量能确认倍数（放量 >= 均量×此值才认）"),
    ),
    code=_MOMENTUM_TREND_CODE,
)


# ─────────────────────────────────────────────────────────────────────────────
# 2. mean_reversion ← mean_reversion_pullback
# ─────────────────────────────────────────────────────────────────────────────

_MEAN_REVERSION_CODE = '''
class MeanReversionStrategy(Strategy):
    # 均值回归：z-score 触下轨买入，回中线卖出。long-only 现货。
    def __init__(
        self, name, clock, msgbus, instrument_id, timeframe="1h",
        period=20, entry_z=2.0, exit_z=0.5, trade_size=0.01,
        position_pct=None, initial_cash=0.0,
    ):
        if period < 2:
            raise ValueError("period must be >= 2")
        super().__init__(name, clock, msgbus)
        self._instrument_id = instrument_id
        self._timeframe = timeframe
        self._period = period
        self._entry_z = entry_z
        self._exit_z = exit_z
        self._trade_size = trade_size
        self._position_pct = position_pct
        self._initial_cash = initial_cash
        self._closes = deque(maxlen=period)
        self._is_long = False
        self._open_qty = 0.0

    def on_start(self):
        self.subscribe_bars(self._instrument_id, self._timeframe)

    def on_bar(self, bar):
        if bar.instrument_id != self._instrument_id:
            return
        if bar.timeframe != self._timeframe:
            return
        self._closes.append(bar.close)
        if len(self._closes) < self._period:
            return
        mean = sum(self._closes) / self._period
        var = sum((x - mean) ** 2 for x in self._closes) / self._period
        std = var ** 0.5
        if std <= 0:
            return
        z = (bar.close - mean) / std
        if z <= -self._entry_z and not self._is_long:
            self._submit(OrderSide.BUY, bar)
        elif self._is_long and z >= -self._exit_z:
            self._submit(OrderSide.SELL, bar)

    def on_position_opened(self, event):
        self._is_long = event.quantity > 0
        self._open_qty = abs(event.quantity)

    def on_position_closed(self, event):
        self._is_long = False
        self._open_qty = 0.0

    def _qty(self, bar):
        if (
            self._position_pct is not None and self._position_pct > 0
            and self._initial_cash > 0 and bar.close > 0
        ):
            return (self._initial_cash * self._position_pct) / bar.close / 1.05
        return self._trade_size

    def _submit(self, side, bar):
        if side == OrderSide.SELL and self._open_qty > 0:
            qty = self._open_qty
        else:
            qty = self._qty(bar)
        order = Order(
            client_order_id=ClientOrderId("rev-" + uuid4().hex[:8]),
            instrument_id=self._instrument_id,
            side=side,
            type=OrderType.MARKET,
            quantity=qty,
        )
        self.submit_order(order)
'''.strip()

_MEAN_REVERSION = ArchetypeMeta(
    name="mean_reversion",
    source_archetype="mean_reversion_pullback",
    applies_to_kinds=("mean_reversion", "volatility"),
    description="z-score 超卖触下轨买入、回中线卖出；吃均值回归。",
    when_to_use="主因子是 mean_reversion / volatility；震荡 / 区间市，无强单边趋势",
    when_not_to_use="强单边趋势市（会一路接飞刀）；主因子是 momentum / trend",
    failure_modes=(
        "趋势反转时接飞刀（跌穿后不回归）",
        "时间止损前没等到回归",
        "板块 / 系统性下杀盖过个股回归",
    ),
    compatible_pivots=("momentum_trend", "volatility_contraction", "multi_factor_combine"),
    params=(
        ArchetypeParam("period", 20, "均值 / 标准差窗口"),
        ArchetypeParam("entry_z", 2.0, "入场 z 阈值（跌破 -entry_z 买）"),
        ArchetypeParam("exit_z", 0.5, "出场 z 阈值（回到 -exit_z 以上卖）"),
    ),
    code=_MEAN_REVERSION_CODE,
)


# ─────────────────────────────────────────────────────────────────────────────
# 3. volatility_contraction ← volatility_contraction (VCP)
# ─────────────────────────────────────────────────────────────────────────────

_VOLATILITY_CONTRACTION_CODE = '''
class VolatilityContractionStrategy(Strategy):
    # 波动收缩后顺势突破（VCP）：波动率处近期低位 + 价格突破近高 → 买入；
    # 跌破近低离场。long-only 现货。
    def __init__(
        self, name, clock, msgbus, instrument_id, timeframe="1h",
        period=20, contraction_pct=0.8, breakout_lookback=10, trade_size=0.01,
        position_pct=None, initial_cash=0.0,
    ):
        if period < 2:
            raise ValueError("period must be >= 2")
        super().__init__(name, clock, msgbus)
        self._instrument_id = instrument_id
        self._timeframe = timeframe
        self._period = period
        self._contraction_pct = contraction_pct
        self._breakout_lookback = breakout_lookback
        self._trade_size = trade_size
        self._position_pct = position_pct
        self._initial_cash = initial_cash
        self._closes = deque(maxlen=max(period, breakout_lookback) + 1)
        self._stds = deque(maxlen=period)
        self._is_long = False
        self._open_qty = 0.0

    def on_start(self):
        self.subscribe_bars(self._instrument_id, self._timeframe)

    def on_bar(self, bar):
        if bar.instrument_id != self._instrument_id:
            return
        if bar.timeframe != self._timeframe:
            return
        self._closes.append(bar.close)
        cl = list(self._closes)
        # 预热需同时够 std 窗口(period)和突破窗口(breakout_lookback+1)——
        # 否则 breakout_lookback>period(非默认调参)时 window 会取到欠填充小样本 → 假突破(CR)
        if len(cl) < max(self._period, self._breakout_lookback + 1):
            return
        recent = cl[-self._period:]
        mean = sum(recent) / len(recent)
        var = sum((x - mean) ** 2 for x in recent) / len(recent)
        std = var ** 0.5
        self._stds.append(std)
        window = cl[-self._breakout_lookback - 1:-1]
        if len(window) < 1:
            return
        hi = max(window)
        lo = min(window)
        avg_std = sum(self._stds) / len(self._stds)
        contracted = avg_std > 0 and std <= avg_std * self._contraction_pct
        if not self._is_long and contracted and bar.close > hi:
            self._submit(OrderSide.BUY, bar)
        elif self._is_long and bar.close < lo:
            self._submit(OrderSide.SELL, bar)

    def on_position_opened(self, event):
        self._is_long = event.quantity > 0
        self._open_qty = abs(event.quantity)

    def on_position_closed(self, event):
        self._is_long = False
        self._open_qty = 0.0

    def _qty(self, bar):
        if (
            self._position_pct is not None and self._position_pct > 0
            and self._initial_cash > 0 and bar.close > 0
        ):
            return (self._initial_cash * self._position_pct) / bar.close / 1.05
        return self._trade_size

    def _submit(self, side, bar):
        if side == OrderSide.SELL and self._open_qty > 0:
            qty = self._open_qty
        else:
            qty = self._qty(bar)
        order = Order(
            client_order_id=ClientOrderId("vcp-" + uuid4().hex[:8]),
            instrument_id=self._instrument_id,
            side=side,
            type=OrderType.MARKET,
            quantity=qty,
        )
        self.submit_order(order)
'''.strip()

_VOLATILITY_CONTRACTION = ArchetypeMeta(
    name="volatility_contraction",
    source_archetype="volatility_contraction",
    applies_to_kinds=("volatility",),
    description="波动收缩到近期低位后顺势突破近高买入，跌破近低离场。",
    when_to_use="主因子是 volatility（波动收缩信号）；横盘整理后等突破",
    when_not_to_use="高波动无序市；需要立即建仓的场景（要等收缩 + 突破双条件）",
    failure_modes=(
        "收缩区假突破（突破后立刻回落）",
        "收缩拖太久，资金被时间消耗",
        "波动向下方扩张（突破方向反了）",
    ),
    compatible_pivots=("momentum_trend", "mean_reversion", "multi_factor_combine"),
    params=(
        ArchetypeParam("period", 20, "波动率（标准差）窗口"),
        ArchetypeParam("contraction_pct", 0.8, "收缩判据：当前 std <= 近期均 std × 此值"),
        ArchetypeParam("breakout_lookback", 10, "突破参考的近 N 根高 / 低"),
    ),
    code=_VOLATILITY_CONTRACTION_CODE,
)


# ─────────────────────────────────────────────────────────────────────────────
# 4. multi_factor_combine （Inalpha 特有，无外部源）
# ─────────────────────────────────────────────────────────────────────────────

_MULTI_FACTOR_CODE = '''
class MultiFactorStrategy(Strategy):
    # 多因子合成择时：动量 + 均值回归 + 量能，归一加权打分；分数过阈买、跌破出场阈卖。
    # long-only 现货。各因子方向应按 factor.timing 的 direction 在权重符号上体现。
    def __init__(
        self, name, clock, msgbus, instrument_id, timeframe="1h",
        mom_period=20, rev_period=20, vol_period=20,
        w_mom=1.0, w_rev=1.0, w_vol=0.5, entry_score=0.4, exit_score=0.0,
        trade_size=0.01, position_pct=None, initial_cash=0.0,
    ):
        super().__init__(name, clock, msgbus)
        self._instrument_id = instrument_id
        self._timeframe = timeframe
        self._mom_period = mom_period
        self._rev_period = rev_period
        self._vol_period = vol_period
        self._w_mom = w_mom
        self._w_rev = w_rev
        self._w_vol = w_vol
        self._entry_score = entry_score
        self._exit_score = exit_score
        self._trade_size = trade_size
        self._position_pct = position_pct
        self._initial_cash = initial_cash
        self._warmup = max(mom_period, rev_period, vol_period)
        self._closes = deque(maxlen=self._warmup + 1)
        self._vols = deque(maxlen=vol_period)
        self._is_long = False
        self._open_qty = 0.0

    def on_start(self):
        self.subscribe_bars(self._instrument_id, self._timeframe)

    def _clamp(self, x):
        return max(-1.0, min(1.0, x))

    def on_bar(self, bar):
        if bar.instrument_id != self._instrument_id:
            return
        if bar.timeframe != self._timeframe:
            return
        self._closes.append(bar.close)
        self._vols.append(bar.volume)
        cl = list(self._closes)
        if len(cl) <= self._warmup:
            return
        # 动量因子：近 mom_period 根收益率
        base = cl[-self._mom_period - 1]
        mom = (bar.close - base) / base if base > 0 else 0.0
        # 均值回归因子：-z（超卖 => 正信号）
        recent = cl[-self._rev_period:]
        mean = sum(recent) / len(recent)
        var = sum((x - mean) ** 2 for x in recent) / len(recent)
        std = var ** 0.5
        rev = -((bar.close - mean) / std) if std > 0 else 0.0
        # 量能因子：相对均量
        vmean = sum(self._vols) / len(self._vols)
        vol = (bar.volume / vmean - 1.0) if vmean > 0 else 0.0
        total_w = self._w_mom + self._w_rev + self._w_vol
        if total_w <= 0:
            return
        score = (
            self._w_mom * self._clamp(mom * 10.0)
            + self._w_rev * self._clamp(rev)
            + self._w_vol * self._clamp(vol)
        ) / total_w
        if score >= self._entry_score and not self._is_long:
            self._submit(OrderSide.BUY, bar)
        elif self._is_long and score <= self._exit_score:
            self._submit(OrderSide.SELL, bar)

    def on_position_opened(self, event):
        self._is_long = event.quantity > 0
        self._open_qty = abs(event.quantity)

    def on_position_closed(self, event):
        self._is_long = False
        self._open_qty = 0.0

    def _qty(self, bar):
        if (
            self._position_pct is not None and self._position_pct > 0
            and self._initial_cash > 0 and bar.close > 0
        ):
            return (self._initial_cash * self._position_pct) / bar.close / 1.05
        return self._trade_size

    def _submit(self, side, bar):
        if side == OrderSide.SELL and self._open_qty > 0:
            qty = self._open_qty
        else:
            qty = self._qty(bar)
        order = Order(
            client_order_id=ClientOrderId("mf-" + uuid4().hex[:8]),
            instrument_id=self._instrument_id,
            side=side,
            type=OrderType.MARKET,
            quantity=qty,
        )
        self.submit_order(order)
'''.strip()

_MULTI_FACTOR = ArchetypeMeta(
    name="multi_factor_combine",
    source_archetype="",
    applies_to_kinds=("momentum", "mean_reversion", "volatility", "volume", "trend", "macro"),
    description="动量 + 均值回归 + 量能归一加权合成打分择时；分数过阈开仓。",
    when_to_use="factor.timing 给出多个 stable 因子（不同 kind）想合成一个信号时",
    when_not_to_use="只有单一主因子（用对应专一骨架更清晰）；因子互相高相关（伪分散）",
    failure_modes=(
        "因子间相关性高 = 伪分散，没真正降风险",
        "某因子 decaying 仍在权重里拖累合成分",
        "权重凭感觉拍（应按 rank_ic 强度 / direction 定权重符号）",
    ),
    compatible_pivots=("momentum_trend", "mean_reversion", "volatility_contraction"),
    params=(
        ArchetypeParam("w_mom", 1.0, "动量因子权重（按 factor.timing direction 定符号）"),
        ArchetypeParam("w_rev", 1.0, "均值回归因子权重"),
        ArchetypeParam("w_vol", 0.5, "量能因子权重"),
        ArchetypeParam("entry_score", 0.4, "合成分入场阈值（[-1,1] 归一后）"),
        ArchetypeParam("exit_score", 0.0, "合成分出场阈值"),
    ),
    code=_MULTI_FACTOR_CODE,
)


# ─────────────────────────────────────────────────────────────────────────────
# Registry
# ─────────────────────────────────────────────────────────────────────────────

#: v1 全部原型（顺序即默认展示顺序）。
ARCHETYPES: Final[tuple[ArchetypeMeta, ...]] = (
    _MOMENTUM_TREND,
    _MEAN_REVERSION,
    _VOLATILITY_CONTRACTION,
    _MULTI_FACTOR,
)

_BY_NAME: Final[dict[str, ArchetypeMeta]] = {a.name: a for a in ARCHETYPES}


def get_archetype(name: str) -> ArchetypeMeta | None:
    """按名取原型；不存在返 None。"""
    return _BY_NAME.get(name)


def list_archetypes(
    factor_kinds: tuple[str, ...] | list[str] | None = None,
) -> list[ArchetypeMeta]:
    """列出原型；给 ``factor_kinds`` 时把匹配的排前面（匹配 kind 数多者更靠前）。

    不过滤掉不匹配的——agent 仍可能想看全部；只做相关性排序。
    """
    if not factor_kinds:
        return list(ARCHETYPES)
    wanted = {k.strip().lower() for k in factor_kinds if k and k.strip()}
    if not wanted:
        return list(ARCHETYPES)

    def match_count(a: ArchetypeMeta) -> int:
        return len(wanted.intersection({k.lower() for k in a.applies_to_kinds}))

    # 稳定排序：匹配数降序，原序号升序（match_count=0 的保持原相对序，排在后面）
    indexed = list(enumerate(ARCHETYPES))
    indexed.sort(key=lambda pair: (-match_count(pair[1]), pair[0]))
    return [a for _i, a in indexed]
