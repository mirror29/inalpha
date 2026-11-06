"""Explicit retries preserve the original research scope and audited source snapshot."""
from hashlib import sha256
from uuid import uuid4

import pytest
from inalpha_shared.errors import ConflictError

from inalpha_evolver.api.retry import retry_seed
from inalpha_evolver.governor.seed import SEED_STRATEGY_CODE

from .test_api_contract import _request


def original():
    body = _request()
    parent = {
        "run_id": uuid4(), "seed_strategy_id": body.seed_strategy_id, "budget": body.budget,
        "config": body.config.model_dump(mode="json"),
        "seed_source_snapshot": SEED_STRATEGY_CODE,
        "seed_source_hash": sha256(SEED_STRATEGY_CODE.encode()).hexdigest(),
    }
    return body, parent


def test_retry_uses_original_source_without_resolving_changed_live_candidate():
    body, parent = original()
    seed = retry_seed(body, parent)
    assert seed.source_code == parent["seed_source_snapshot"]
    assert seed.source_hash == parent["seed_source_hash"]


@pytest.mark.parametrize("field", ["as_of", "fee_rate", "params", "budget", "seed"])
def test_retry_cannot_change_original_experiment(field):
    body, parent = original()
    if field == "as_of":
        from datetime import timedelta
        body.config.as_of += timedelta(hours=1)
    elif field == "fee_rate":
        body.config.fee_rate = 0.01
    elif field == "params":
        body.config.params = {"new": 1}
    elif field == "budget":
        body.budget += 1
    else:
        body.seed_strategy_id = "another"
    with pytest.raises(ConflictError) as error:
        retry_seed(body, parent)
    assert error.value.code == "EVOLUTION_RETRY_SCOPE_CHANGED"


def test_corrupt_original_source_hash_fails_closed():
    body, parent = original()
    parent["seed_source_hash"] = "b" * 64
    with pytest.raises(ConflictError) as error:
        retry_seed(body, parent)
    assert error.value.code == "EVOLUTION_RETRY_SOURCE_INVALID"


def test_missing_legacy_snapshot_is_reported_without_a_server_error():
    body, parent = original()
    parent["seed_source_snapshot"] = None
    with pytest.raises(ConflictError) as error:
        retry_seed(body, parent)
    assert error.value.code == "EVOLUTION_RETRY_SNAPSHOT_MISSING"
