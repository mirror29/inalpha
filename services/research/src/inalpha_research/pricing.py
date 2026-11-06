"""Operator-supplied, model-bound estimates; never substitute invoices or guess rates."""
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ResearchUsagePricing(BaseModel):
    """Freeze explicit USD rates for exactly one requested provider/model pair."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    version: str = Field(min_length=1)
    currency: Literal["USD"] = "USD"
    input_usd_per_million: Decimal = Field(ge=0, allow_inf_nan=False)
    output_usd_per_million: Decimal = Field(ge=0, allow_inf_nan=False)


def pricing_for_model(
    snapshots: list[ResearchUsagePricing], provider: str, model: str
) -> dict[str, object] | None:
    """Unknown or ambiguous matches remain unknown; return a detached receipt snapshot."""
    matches = [p for p in snapshots if p.provider == provider and p.model == model]
    if len(matches) != 1:
        return None
    return matches[0].model_dump(mode="json", exclude={"provider", "model"})
