"""`RiskRule` 抽象 + 配套数据结构。

设计参考 [refs/freqtrade.md §6.2](../../../../../docs/miro/refs/freqtrade.md)
（freqtrade `plugins/protections/iprotection.py` 范式，借鉴设计 + 中文化重写，
freqtrade 是 GPL-3.0 但本文件**不复制源码**，仅借鉴抽象，License 用与本仓库一致）。

3 层拦截能力（比 freqtrade 多一层 `market`，对应 Inalpha 多市场需求）：

- `has_global_check` —— 全局拦截（如账户级 drawdown）
- `has_market_check` —— 按市场拦截（A股盘后 / 美股盘前等）
- `has_symbol_check` —— 单 symbol 拦截
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, ClassVar, Literal, Protocol, runtime_checkable

from ...kernel.identifiers import InstrumentId

# ─── 类型别名 ───

Side = Literal["long", "short", "*"]
"""锁方向。`*` = 双向锁。"""

LockScope = Literal["global", "market", "symbol"]
"""锁范围。global > market > symbol。"""


# ─── 配置异常 ───


class RiskRuleConfigError(ValueError):
    """配置错误（如同时指定 `stop_duration_min` 与 `unlock_at`）。启动时 fail-fast。"""


# ─── 数据结构 ───


@dataclass(frozen=True, slots=True)
class RiskVerdict:
    """风控触发后的判定结果。

    **未触发返回 `None`**，不是 `RiskVerdict(...)` 实例 —— 减少调用方分支。
    """

    until: datetime
    """锁定到何时（UTC tz-aware）。"""

    reason: str
    """人类可读，写入 `Order.reason` 和审计日志。"""

    rule_name: str
    """触发规则名（用于审计追溯）。"""

    lock_side: Side = "*"
    lock_scope: LockScope = "symbol"
    lock_market: str | None = None
    """当 scope='market' 时使用。venue 字符串（如 'binance' / 'nasdaq'）。"""


@dataclass(frozen=True, slots=True)
class ClosedTradeRecord:
    """已平仓 Trade 的最小信息（RiskRule 用，不绑 Inalpha Order/Trade ORM）。

    Slice 4 接 storage 时由 `TradeRepository` 实现层填充。
    """

    instrument_id: InstrumentId
    side: Side
    open_ts: datetime
    close_ts: datetime
    close_profit_pct: float
    """净盈亏百分比（如 -0.03 表示 -3%）。"""
    close_profit_abs: float
    """净盈亏绝对值（账户币种）。"""
    exit_reason: str
    """成交退出原因：'stop_loss' / 'trailing_stop_loss' / 'take_profit' / 'manual' / 'liquidation' / ..."""


@runtime_checkable
class TradeRepository(Protocol):
    """Trade 历史查询接口。

    `RiskRule` 子类**只通过此 Protocol 访问历史 trade**，不直接 import storage 模块。
    Slice 1 用 mock 实现做单测，Slice 4 接 PostgreSQL 实现。
    """

    def get_closed_trades(
        self,
        *,
        instrument_id: InstrumentId | None = None,
        close_after: datetime,
        side: Side | None = None,
        exit_reasons: list[str] | None = None,
        max_profit_pct: float | None = None,
    ) -> list[ClosedTradeRecord]:
        """查 `close_after` 之后已平仓的 trades，按 `close_ts` 升序。

        Args:
            instrument_id: 限定 symbol；None 表示全部
            close_after: 平仓时间下界（含）
            side: 限定方向；None / "*" 表示双向
            exit_reasons: 限定退出原因（如 ["stop_loss", "trailing_stop_loss"]）；None 表示不过滤
            max_profit_pct: 仅返回盈亏 < 此值的 trade（用于 stoploss_guard 找亏损）；None 表示不过滤
        """
        ...


# ─── 抽象基类 ───


class RiskRule(ABC):
    """风控规则抽象基类。

    子类必须：
    1. override `has_*_check` 至少一个为 `True`
    2. override 对应的 `check_*` 方法（默认实现返回 `None`）
    3. implement `short_desc`
    """

    has_global_check: ClassVar[bool] = False
    has_market_check: ClassVar[bool] = False
    has_symbol_check: ClassVar[bool] = False

    def __init__(self, config: dict[str, Any], trade_repo: TradeRepository) -> None:
        self._config = config
        self._trade_repo = trade_repo
        self._stop_duration_min, self._unlock_at = self._parse_duration(config)
        self._lookback_min = int(config.get("lookback_min", 60))
        if self._lookback_min <= 0:
            raise RiskRuleConfigError(
                f"{self.name}: lookback_min must be positive, got {self._lookback_min}"
            )

    # ─── 元信息 ───

    @property
    def name(self) -> str:
        return self.__class__.__name__

    @abstractmethod
    def short_desc(self) -> str:
        """启动日志用的一行描述。"""

    # ─── 3 层拦截接口（子类按需 override）───

    def check_global(
        self, now: datetime, side: Side, starting_balance: float
    ) -> RiskVerdict | None:
        """全局拦截。默认 `None`；`has_global_check=True` 的子类必须 override。"""
        return None

    def check_market(
        self, market: str, now: datetime, side: Side, starting_balance: float
    ) -> RiskVerdict | None:
        """按市场拦截。默认 `None`。`market` 用 venue 字符串。"""
        return None

    def check_symbol(
        self,
        instrument_id: InstrumentId,
        now: datetime,
        side: Side,
        starting_balance: float,
    ) -> RiskVerdict | None:
        """单 symbol 拦截。默认 `None`。"""
        return None

    # ─── 基类工具 ───

    def calculate_lock_end(
        self, trades: list[ClosedTradeRecord], now: datetime
    ) -> datetime:
        """从 trades 末尾 + stop_duration / unlock_at 算解锁时刻。

        子类不应 override —— 保证所有 RiskRule 解锁逻辑一致。
        """
        if not trades:
            base_time = now
        else:
            base_time = max(t.close_ts for t in trades)

        if self._unlock_at is not None:
            hour, minute = self._unlock_at
            unlock_dt = base_time.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if unlock_dt <= base_time:
                unlock_dt += timedelta(days=1)
            return unlock_dt

        return base_time + timedelta(minutes=self._stop_duration_min)

    # ─── 配置解析 ───

    @staticmethod
    def _parse_duration(config: dict[str, Any]) -> tuple[int, tuple[int, int] | None]:
        """解析锁定时长。`stop_duration_min` 与 `unlock_at` **二选一**，矛盾 fail-fast。

        Returns:
            (stop_duration_min, unlock_at_hhmm)
            unlock_at_hhmm 不为 None 时使用 unlock_at 路径；否则用 stop_duration_min。
        """
        has_duration = "stop_duration_min" in config
        has_unlock_at = "unlock_at" in config

        if has_duration and has_unlock_at:
            raise RiskRuleConfigError(
                "RiskRule: stop_duration_min 与 unlock_at 不能同时配置"
            )

        if has_unlock_at:
            unlock_str = str(config["unlock_at"])
            try:
                hour_str, minute_str = unlock_str.split(":")
                hour, minute = int(hour_str), int(minute_str)
                if not (0 <= hour < 24 and 0 <= minute < 60):
                    raise ValueError
            except ValueError as e:
                raise RiskRuleConfigError(
                    f"RiskRule: unlock_at 必须是 'HH:MM' 格式，got {unlock_str!r}"
                ) from e
            return 60, (hour, minute)  # stop_duration_min 兜底无意义

        duration = int(config.get("stop_duration_min", 60))
        if duration <= 0:
            raise RiskRuleConfigError(
                f"RiskRule: stop_duration_min must be positive, got {duration}"
            )
        return duration, None
