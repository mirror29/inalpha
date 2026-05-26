"""配置 schema + TOML 加载 + Rule 工厂。"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from inalpha_paper.execution.risk_rules import (
    ClosedTradeRecord,
    CooldownRule,
    LowProfitRule,
    MarketHoursRule,
    MaxDrawdownRule,
    RiskRulesConfig,
    StoplossGuardRule,
    build_rules,
    load_risk_rules_config,
)
from inalpha_paper.execution.risk_rules.base import Side
from inalpha_paper.execution.risk_rules.config import (
    CooldownRuleConfig,
    LowProfitRuleConfig,
)

# ─── Mocks ───


class _Repo:
    def get_closed_trades(self, **kwargs: object) -> list[ClosedTradeRecord]:
        return []


class _Calendar:
    def is_trading_hours(
        self, market: str, now: datetime, *, include_pre: bool = False,
        include_after: bool = False,
    ) -> bool:
        return True

    def next_session_open(self, market: str, now: datetime) -> datetime:
        return now


# ─── Pydantic schema 基本 ───


def test_default_config_empty() -> None:
    cfg = RiskRulesConfig()
    assert cfg.enabled is True
    assert cfg.starting_balance == 10_000.0
    assert cfg.rules == []


def test_unknown_top_level_field_rejected() -> None:
    with pytest.raises(ValidationError, match="Extra inputs"):
        RiskRulesConfig.model_validate({"enabled": True, "foo": "bar"})


def test_unknown_rule_field_rejected() -> None:
    with pytest.raises(ValidationError):
        RiskRulesConfig.model_validate(
            {"rules": [{"name": "CooldownRule", "typo_field": 1}]}
        )


# ─── discriminator union ───


def test_discriminator_picks_right_subclass() -> None:
    cfg = RiskRulesConfig.model_validate(
        {
            "rules": [
                {"name": "CooldownRule", "stop_duration_min": 30},
                {"name": "LowProfitRule", "trade_limit": 4, "required_profit": -0.05},
            ]
        }
    )
    assert len(cfg.rules) == 2
    assert isinstance(cfg.rules[0], CooldownRuleConfig)
    assert isinstance(cfg.rules[1], LowProfitRuleConfig)
    assert cfg.rules[1].trade_limit == 4


def test_unknown_rule_name_rejected() -> None:
    with pytest.raises(ValidationError, match=r"union_tag_invalid|expected tags"):
        RiskRulesConfig.model_validate({"rules": [{"name": "FakeRule"}]})


# ─── 配置约束 ───


def test_duration_and_unlock_at_conflict() -> None:
    with pytest.raises(ValidationError, match="不能同时显式配置"):
        RiskRulesConfig.model_validate(
            {
                "rules": [
                    {
                        "name": "CooldownRule",
                        "stop_duration_min": 30,
                        "unlock_at": "13:00",
                    }
                ]
            }
        )


def test_unlock_at_invalid_format() -> None:
    with pytest.raises(ValidationError, match=r"HH:MM|时间范围越界"):
        RiskRulesConfig.model_validate(
            {"rules": [{"name": "MarketHoursRule", "unlock_at": "25:00"}]}
        )


def test_negative_lookback_rejected() -> None:
    with pytest.raises(ValidationError):
        RiskRulesConfig.model_validate(
            {"rules": [{"name": "CooldownRule", "lookback_min": -5}]}
        )


def test_max_drawdown_out_of_range() -> None:
    with pytest.raises(ValidationError):
        RiskRulesConfig.model_validate(
            {"rules": [{"name": "MaxDrawdownRule", "max_drawdown": 1.5}]}
        )


# ─── TOML 加载 ───


def test_load_real_config_file() -> None:
    """加载 services/paper/configs/risk_rules.toml 实际默认配置。"""
    repo_root = Path(__file__).resolve().parent.parent
    cfg_path = repo_root / "configs" / "risk_rules.toml"
    cfg = load_risk_rules_config(cfg_path)

    assert cfg.enabled is True
    assert len(cfg.rules) == 5
    rule_names = [r.name for r in cfg.rules]
    assert "MaxDrawdownRule" in rule_names
    assert "StoplossGuardRule" in rule_names
    assert "MarketHoursRule" in rule_names
    assert "CooldownRule" in rule_names
    assert "LowProfitRule" in rule_names


def test_load_from_temp_toml(tmp_path: Path) -> None:
    """临时 TOML 文件加载。"""
    cfg_file = tmp_path / "test.toml"
    cfg_file.write_text(
        'enabled = true\n'
        '[[rules]]\n'
        'name = "CooldownRule"\n'
        'lookback_min = 10\n'
        'stop_duration_min = 5\n'
    )
    cfg = load_risk_rules_config(cfg_file)
    assert len(cfg.rules) == 1
    assert isinstance(cfg.rules[0], CooldownRuleConfig)
    assert cfg.rules[0].lookback_min == 10


# ─── build_rules 工厂 ───


def test_build_rules_creates_correct_types() -> None:
    cfg = RiskRulesConfig.model_validate(
        {
            "rules": [
                {"name": "CooldownRule"},
                {"name": "LowProfitRule", "trade_limit": 3, "required_profit": -0.05},
                {"name": "MaxDrawdownRule"},
                {"name": "StoplossGuardRule"},
                {"name": "MarketHoursRule"},
            ]
        }
    )
    rules = build_rules(cfg, trade_repo=_Repo(), market_calendar=_Calendar())

    assert len(rules) == 5
    assert isinstance(rules[0], CooldownRule)
    assert isinstance(rules[1], LowProfitRule)
    assert isinstance(rules[2], MaxDrawdownRule)
    assert isinstance(rules[3], StoplossGuardRule)
    assert isinstance(rules[4], MarketHoursRule)


def test_build_rules_market_hours_requires_calendar() -> None:
    cfg = RiskRulesConfig.model_validate(
        {"rules": [{"name": "MarketHoursRule"}]}
    )
    with pytest.raises(ValueError, match="market_calendar"):
        build_rules(cfg, trade_repo=_Repo(), market_calendar=None)


def test_build_rules_disabled_returns_empty() -> None:
    cfg = RiskRulesConfig.model_validate(
        {
            "enabled": False,
            "rules": [{"name": "CooldownRule"}],
        }
    )
    rules = build_rules(cfg, trade_repo=_Repo())
    assert rules == []


def test_build_rules_propagates_config_values() -> None:
    cfg = RiskRulesConfig.model_validate(
        {
            "rules": [
                {"name": "LowProfitRule", "trade_limit": 7, "required_profit": -0.08},
            ]
        }
    )
    rules = build_rules(cfg, trade_repo=_Repo())
    rule = rules[0]
    assert isinstance(rule, LowProfitRule)

    assert rule._trade_limit == 7  # type: ignore[attr-defined]
    assert rule._required_profit == -0.08  # type: ignore[attr-defined]


# ─── 端到端：load + build ───


def test_end_to_end_load_and_build_default_config() -> None:
    repo_root = Path(__file__).resolve().parent.parent
    cfg = load_risk_rules_config(repo_root / "configs" / "risk_rules.toml")
    rules = build_rules(cfg, trade_repo=_Repo(), market_calendar=_Calendar())
    assert len(rules) == 5

    # 验证 short_desc 全可调（启动日志会用）
    for rule in rules:
        desc = rule.short_desc()
        assert isinstance(desc, str)
        assert len(desc) > 0


def _unused_side_marker() -> Side:
    return "*"
