"""Pricing remains explicit, finite, immutable and tied to the selected model."""
from decimal import Decimal

import pytest
from inalpha_shared.usage import estimate_cost
from pydantic import ValidationError

from inalpha_research.pricing import ResearchUsagePricing, pricing_for_model


def snapshot(**overrides):
    return ResearchUsagePricing(**{
        "provider": "deepseek", "model": "deepseek-flash", "version": "acceptance-v1",
        "input_usd_per_million": "0.3", "output_usd_per_million": "1.2", **overrides,
    })


def test_model_binding_and_detached_price():
    source = snapshot()
    price = pricing_for_model([source], "deepseek", "deepseek-flash")
    assert estimate_cost({"usage_status": "known", "input_tokens": 1000, "output_tokens": 100}, price) == Decimal("0.00042")
    price["version"] = "changed"
    assert source.version == "acceptance-v1"
    assert pricing_for_model([source], "openai", "deepseek-flash") is None
    assert pricing_for_model([source], "deepseek", "another-model") is None
    assert pricing_for_model([source, source], "deepseek", "deepseek-flash") is None
    assert pricing_for_model([], "deepseek", "deepseek-flash") is None


@pytest.mark.parametrize("rate", ["-1", "NaN", "Infinity"])
def test_invalid_rates_rejected(rate):
    with pytest.raises(ValidationError):
        snapshot(input_usd_per_million=rate)
