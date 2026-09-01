"""短效演化审批断言测试。"""

from __future__ import annotations

import base64
import time
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from fastapi import HTTPException

from inalpha_evolver.api.approval import verify_evolution_approval

_DIGEST = "a" * 64
_REQUEST_DIGEST = "b" * 64
_PRIVATE_KEY = Ed25519PrivateKey.generate()
_PUBLIC_KEY_B64 = base64.b64encode(
    _PRIVATE_KEY.public_key().public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
).decode()


def _token(
    ttl_seconds: int,
    *,
    overrides: dict[str, object] | None = None,
    private_key: Ed25519PrivateKey = _PRIVATE_KEY,
) -> str:
    now = int(time.time())
    payload: dict[str, object] = {
        "sub": "user:alice",
        "token_use": "evolution_credential",
        "aud": ["inalpha-evolver", "inalpha-dashboard-credential"],
        "jti": "11111111-1111-4111-8111-111111111111",
        "operation_id": "approval-operation-1",
        "config_id": "config-1",
        "provider": "deepseek",
        "grant_purpose": "e1_run",
        "llm_config_digest": _DIGEST,
        "request_digest": _REQUEST_DIGEST,
        "iat": now,
        "exp": now + ttl_seconds,
    }
    payload.update(overrides or {})
    return jwt.encode(
        payload,
        private_key,
        algorithm="EdDSA",
    )


def _verify(token: str) -> None:
    verify_evolution_approval(
        token,
        owner_sub="user:alice",
        operation_id="approval-operation-1",
        config_id="config-1",
        provider="deepseek",
        llm_config_digest=_DIGEST,
        request_digest=_REQUEST_DIGEST,
        grant_purpose="e1_run",
        settings=SimpleNamespace(  # type: ignore[arg-type]
            evolution_credential_public_key_b64=_PUBLIC_KEY_B64
        ),
    )


def test_approval_accepts_only_bounded_matching_scope() -> None:
    _verify(_token(30 * 60 * 60))

    with pytest.raises(HTTPException) as error:
        _verify(_token(30 * 60 * 60 + 1))
    assert error.value.status_code == 403


def test_approval_rejects_another_owner() -> None:
    now = int(time.time())
    token = jwt.encode(
        {
            "sub": "user:bob",
            "token_use": "evolution_credential",
            "aud": ["inalpha-evolver", "inalpha-dashboard-credential"],
            "jti": "11111111-1111-4111-8111-111111111111",
            "operation_id": "approval-operation-1",
            "config_id": "config-1",
            "provider": "deepseek",
            "llm_config_digest": _DIGEST,
            "request_digest": _REQUEST_DIGEST,
            "iat": now,
            "exp": now + 300,
        },
        _PRIVATE_KEY,
        algorithm="EdDSA",
    )

    with pytest.raises(HTTPException) as error:
        _verify(token)
    assert error.value.status_code == 403


@pytest.mark.parametrize(
    "overrides",
    [
        {"token_use": "session"},
        {"operation_id": "another-operation"},
        {"config_id": "config-2"},
        {"provider": "openai"},
        {"grant_purpose": "event_campaign"},
        {"llm_config_digest": "b" * 64},
        {"request_digest": "c" * 64},
        {"iat": None},
        {"exp": None},
    ],
)
def test_approval_rejects_invalid_claims(overrides: dict[str, object]) -> None:
    with pytest.raises(HTTPException) as error:
        _verify(_token(300, overrides=overrides))
    assert error.value.status_code in {401, 403}


def test_approval_rejects_expired_bad_signature_and_non_positive_ttl() -> None:
    tokens = [
        _token(-1),
        _token(300, private_key=Ed25519PrivateKey.generate()),
        _token(300, overrides={"iat": int(time.time()) + 300}),
    ]
    for token in tokens:
        with pytest.raises(HTTPException) as error:
            _verify(token)
        assert error.value.status_code in {401, 403}
