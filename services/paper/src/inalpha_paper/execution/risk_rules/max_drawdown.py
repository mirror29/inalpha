"""`MaxDrawdownRule` —— 全局账户回撤超阈值即停所有开仓。

[ADR-0006 §D2](../../../../../docs/miro/decisions/0006-risk-rules.md) 表中第 3 件。
直翻 freqtrade `MaxDrawdown`，**强制 equity 模式**（禁用 freqtrade legacy `ratios`
模式，ADR §D2 / §关键约定 3）。

**触发条件**：lookback 窗口内 equity curve 回撤超过 `max_drawdown` → 锁全局。
**用途**：账户级回撤一票否决（运行时实时拦截，对应 ADR-0020 §D4 fitness 回撤一票否决的实盘版）。
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from .base import RiskRule, RiskRuleConfigError, RiskVerdict, Side, TradeRepository


class MaxDrawdownRule(RiskRule):
    has_global_check = True

    def __init__(self, config: dict[str, Any], trade_repo: TradeRepository) -> None:
        super().__init__(config, trade_repo)
        self._max_drawdown = float(config.get("max_drawdown", 0.15))
        self._trade_limit = int(config.get("trade_limit", 1))
        if not (0.0 < self._max_drawdown <= 1.0):
            raise RiskRuleConfigError(
                f"{self.name}: max_drawdown must be in (0, 1], got {self._max_drawdown}"
            )

    def short_desc(self) -> str:
        return (
            f"{self.name} - 账户回撤 > {self._max_drawdown:.2%} 即停"
            f"（窗口 {self._lookback_min} 分钟）"
        )

    def check_global(
        self, now: datetime, side: Side, starting_balance: float
    ) -> RiskVerdict | None:
        window_start = now - timedelta(minutes=self._lookback_min)
        trades_in_window = self._trade_repo.get_closed_trades(close_after=window_start)
        if len(trades_in_window) < self._trade_limit:
            return None

        # window 之前的累计 abs 盈亏作为实际起点（避免按"启动余额"算回撤偏小）
        trades_before = self._trade_repo.get_closed_trades(
            close_after=datetime(1970, 1, 1, tzinfo=now.tzinfo),
            close_before=window_start,
        )
        profit_before = sum(t.close_profit_abs for t in trades_before)
        base_balance = starting_balance + profit_before

        drawdown = self._equity_max_drawdown(trades_in_window, base_balance)
        if drawdown <= self._max_drawdown:
            return None

        until = self.calculate_lock_end(trades_in_window, now)
        return RiskVerdict(
            until=until,
            reason=f"账户回撤 {drawdown:.2%} > {self._max_drawdown:.2%}",
            rule_name=self.name,
            lock_side="*",
            lock_scope="global",
        )

    @staticmethod
    def _equity_max_drawdown(trades: list, base_balance: float) -> float:
        """Equity curve max drawdown（peak-to-trough 相对幅度）。"""
        equity = base_balance
        peak = base_balance
        max_dd = 0.0
        for t in trades:
            equity += t.close_profit_abs
            if equity > peak:
                peak = equity
            if peak > 0:
                dd = (peak - equity) / peak
                if dd > max_dd:
                    max_dd = dd
        return max_dd
