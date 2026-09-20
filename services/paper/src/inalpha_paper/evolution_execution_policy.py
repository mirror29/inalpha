"""Versioned, serializable protection settings for frozen research execution."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .config import PaperSettings


class EvolutionExecutionPolicy(BaseModel):
    """Only engine protection options; no credentials or mutable service configuration."""

    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)
    version: Literal["paper-protection-v1"] = "paper-protection-v1"
    protective_stop_loss_pct: float | None = Field(default=None, gt=0, le=1)
    protective_take_profit_pct: float | None = Field(default=None, gt=0)
    protective_trailing_stop_pct: float | None = Field(default=None, gt=0)
    protective_chandelier_atr_mult: float | None = Field(default=None, gt=0)
    protective_chandelier_atr_period: int = Field(default=22, ge=1)

    @classmethod
    def from_settings(cls, settings: PaperSettings):
        """Copy the effective Paper policy once; future settings changes cannot alter it."""
        return cls(**{key: getattr(settings, key) for key in cls.model_fields if key != "version"})

    def engine_kwargs(self) -> dict:
        """Translate only validated protection fields into the engine call."""
        return self.model_dump(exclude={"version"})


def frozen_protection_kwargs(config: dict) -> dict:
    """Legacy runs keep their original unguarded semantics; new runs use their frozen policy."""
    if "protection_policy" not in config:
        return {}
    return EvolutionExecutionPolicy.model_validate(config["protection_policy"]).engine_kwargs()
