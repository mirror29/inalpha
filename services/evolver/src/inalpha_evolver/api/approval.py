"""Orchestration Ed25519 evolution capability verification."""

from __future__ import annotations

import base64

import jwt
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import load_der_public_key
from fastapi import HTTPException

from ..config import EvolverSettings

_GRANT_AUDIENCE = "inalpha-evolver"
_MAX_GRANT_TTL_SECONDS = 30 * 60 * 60


def verify_evolution_approval(
    token: str,
    *,
    owner_sub: str,
    operation_id: str,
    config_id: str,
    provider: str,
    llm_config_digest: str,
    request_digest: str,
    grant_purpose: str,
    settings: EvolverSettings,
) -> None:
    """Verify one owner/request-bound grant without giving Evolver signing authority."""
    try:
        encoded_key = settings.evolution_credential_public_key_b64.strip()
        if not encoded_key:
            raise HTTPException(status_code=503, detail="evolution grant verifier unavailable")
        loaded_key = load_der_public_key(base64.b64decode(encoded_key, validate=True))
        if not isinstance(loaded_key, Ed25519PublicKey):
            raise ValueError("evolution grant key must be Ed25519")
        public_key = loaded_key
        payload = jwt.decode(
            token,
            public_key,
            algorithms=["EdDSA"],
            audience=_GRANT_AUDIENCE,
            options={"require": ["sub", "jti", "aud", "exp", "iat"]},
        )
    except HTTPException:
        raise
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=503, detail="evolution grant verifier unavailable") from exc
    except jwt.PyJWTError as exc:
        raise HTTPException(status_code=401, detail="invalid evolution grant") from exc
    expected = {
        "token_use": "evolution_credential",
        "sub": owner_sub,
        "operation_id": operation_id,
        "config_id": config_id,
        "provider": provider,
        "grant_purpose": grant_purpose,
        "llm_config_digest": llm_config_digest,
        "request_digest": request_digest,
    }
    issued_at = payload.get("iat")
    expires_at = payload.get("exp")
    invalid_ttl = (
        not isinstance(issued_at, int)
        or not isinstance(expires_at, int)
        or expires_at - issued_at > _MAX_GRANT_TTL_SECONDS
        or expires_at <= issued_at
    )
    if invalid_ttl or any(payload.get(key) != value for key, value in expected.items()):
        raise HTTPException(status_code=403, detail="evolution grant scope mismatch")


__all__ = ["verify_evolution_approval"]
