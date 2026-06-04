"""Pydantic 配置 schema + TOML 加载 + Rule 工厂。

[ADR-0006 §D4](../../../../../docs/miro/decisions/0006-risk-rules.md) Pydantic 配置驱动。
TOML 格式（用 Python 标准库 `tomllib`，零新依赖）。配置文件示例见
[`services/paper/configs/risk_rules.toml`](../../../../configs/risk_rules.toml)。

加载链路：

```
configs/risk_rules.toml
    │ tomllib.load()
    ▼
dict[str, Any]
    │ RiskRulesConfig.model_validate()
    ▼
RiskRulesConfig（Pydantic 校验 + discriminated union）
    │ build_rules(trade_repo, market_calendar)
    ▼
list[RiskRule]（带 trade_repo / calendar 注入完毕）
    │ RiskEngine(rules=...)
```
"""
from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .base import MarketCalendar, RiskRule, TradeRepository
from .cooldown import CooldownRule
from .low_profit import LowProfitRule
from .market_hours import MarketHoursRule
from .max_drawdown import MaxDrawdownRule
from .stoploss_guard import StoplossGuardRule

# ─── 共享基类 ───


class _RuleConfigBase(BaseModel):
    """所有 RiskRule 共享字段。"""

    model_config = ConfigDict(extra="forbid")
    """禁止未知字段（防 typo 隐藏 bug）。"""

    lookback_min: int = Field(default=60, gt=0)
    stop_duration_min: int | None = Field(default=None, gt=0)
    unlock_at: str | None = None
    """`'HH:MM'` 格式。与 stop_duration_min 互斥。"""

    @model_validator(mode="after")
    def _validate_duration_xor(self) -> _RuleConfigBase:
        set_fields = self.__pydantic_fields_set__
        explicit_duration = "stop_duration_min" in set_fields
        explicit_unlock_at = "unlock_at" in set_fields
        if explicit_duration and explicit_unlock_at:
            raise ValueError("stop_duration_min 与 unlock_at 不能同时显式配置")
        if self.unlock_at is not None:
            parts = self.unlock_at.split(":")
            if len(parts) != 2:
                raise ValueError(f"unlock_at 必须是 'HH:MM' 格式，got {self.unlock_at!r}")
            try:
                hour, minute = int(parts[0]), int(parts[1])
            except ValueError as e:
                raise ValueError(
                    f"unlock_at 必须是 'HH:MM' 数字，got {self.unlock_at!r}"
                ) from e
            if not (0 <= hour < 24 and 0 <= minute < 60):
                raise ValueError(f"unlock_at 时间范围越界：{self.unlock_at!r}")
        return self


# ─── 每个 rule 一个 model ───


class CooldownRuleConfig(_RuleConfigBase):
    name: Literal["CooldownRule"]
    stop_duration_min: int = Field(default=60, gt=0)


class LowProfitRuleConfig(_RuleConfigBase):
    name: Literal["LowProfitRule"]
    trade_limit: int = Field(default=1, gt=0)
    required_profit: float = 0.0
    only_per_side: bool = False
    stop_duration_min: int = Field(default=60, gt=0)


class MaxDrawdownRuleConfig(_RuleConfigBase):
    name: Literal["MaxDrawdownRule"]
    max_drawdown: float = Field(default=0.15, gt=0.0, le=1.0)
    trade_limit: int = Field(default=1, gt=0)
    stop_duration_min: int = Field(default=240, gt=0)


class StoplossGuardRuleConfig(_RuleConfigBase):
    name: Literal["StoplossGuardRule"]
    trade_limit: int = Field(default=10, gt=0)
    only_per_symbol: bool = False
    only_per_side: bool = False
    required_profit: float = 0.0
    stop_duration_min: int = Field(default=120, gt=0)


class MarketHoursRuleConfig(_RuleConfigBase):
    name: Literal["MarketHoursRule"]
    allow_pre_market: bool = False
    allow_after_hours: bool = False
    stop_duration_min: int = Field(default=1, gt=0)
    """MarketHoursRule 解锁由 calendar 决定，本字段实际未用但 base 校验需要。"""


# 注：discriminated union 用 Annotated[Union[...], Field(discriminator="name")]
RuleConfig = Annotated[
    CooldownRuleConfig
    | LowProfitRuleConfig
    | MaxDrawdownRuleConfig
    | StoplossGuardRuleConfig
    | MarketHoursRuleConfig,
    Field(discriminator="name"),
]


# ─── 顶层 ───


class RiskRulesConfig(BaseModel):
    """顶层风控配置。"""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    starting_balance: float = Field(default=10_000.0, gt=0)
    max_order_notional: float | None = Field(default=None, gt=0)
    """单笔下单名义价值（``quantity * ref_price``）硬上限，无状态前置校验（issue #42）。

    与 ``rules`` 的行为型锁规则正交：这是 per-order、stateless 的"防胖手指 / 防策略
    算错 quantity"安全上限，超限只拒**这一笔**、不锁 symbol。``None`` = 不限制。
    以订单**计价货币**（quote currency）计——跨币种精确折算留 follow-up；作为粗粒度
    安全上限已足够（拦得住 ``quantity=1e9`` 这类，又不挡正常交易）。
    """
    rules: list[RuleConfig] = Field(default_factory=list)


# ─── 加载 ───


def load_risk_rules_config(path: Path | str) -> RiskRulesConfig:
    """从 TOML 文件加载并校验。出错 raise pydantic ValidationError（fail-fast）。"""
    path = Path(path)
    with path.open("rb") as f:
        data = tomllib.load(f)
    return RiskRulesConfig.model_validate(data)


# ─── 工厂 ───


_RULE_REGISTRY: dict[str, type[RiskRule]] = {
    "CooldownRule": CooldownRule,
    "LowProfitRule": LowProfitRule,
    "MaxDrawdownRule": MaxDrawdownRule,
    "StoplossGuardRule": StoplossGuardRule,
    "MarketHoursRule": MarketHoursRule,
}


def build_rules(
    config: RiskRulesConfig,
    *,
    trade_repo: TradeRepository,
    market_calendar: MarketCalendar | None = None,
) -> list[RiskRule]:
    """把 `RiskRulesConfig.rules` 实例化为 `RiskRule` 列表。

    Raises:
        ValueError: 当配置含 `MarketHoursRule` 但未提供 `market_calendar`
    """
    if not config.enabled:
        return []

    rules: list[RiskRule] = []
    for rc in config.rules:
        rule_cls = _RULE_REGISTRY[rc.name]
        # user 显式选择 unlock_at 时不传 stop_duration_min（避免 RiskRule._parse_duration 报冲突）
        exclude: set[str] = {"name"}
        if "unlock_at" in rc.__pydantic_fields_set__:
            exclude.add("stop_duration_min")
        else:
            exclude.add("unlock_at")
        rule_config: dict[str, Any] = rc.model_dump(exclude=exclude, exclude_none=True)
        if rc.name == "MarketHoursRule":
            if market_calendar is None:
                raise ValueError(
                    "RiskRulesConfig.rules 含 MarketHoursRule 但未提供 market_calendar"
                )
            rule = MarketHoursRule(rule_config, trade_repo, market_calendar)
        else:
            rule = rule_cls(rule_config, trade_repo)
        rules.append(rule)
    return rules
