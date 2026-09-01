"""共享的冻结 LLM 快照与审批断言测试夹具。"""

from __future__ import annotations

import base64
import copy
import time

import jwt
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

VALID_LLM_SNAPSHOT = {
    "config_id": "config-1",
    "provider": "deepseek",
    "model": "deepseek-v4-pro",
    "base_url": "https://api.deepseek.com",
    "pricing": {
        "version": "provider-estimate-2026-08",
        "currency": "USD",
        "input_usd_per_million": 0.56,
        "output_usd_per_million": 1.68,
        "assumed_input_tokens": 24_000,
        "max_output_tokens": 8_192,
        "estimated_max_usd_per_candidate": 0.02720256,
    },
    "config_digest": "a4635b0c80f69b6054bdc2330b78cb98d9c81c849d476e7d01f1b8d626015c2c",
}

EVOLUTION_GRANT_PRIVATE_KEY = Ed25519PrivateKey.generate()
EVOLUTION_GRANT_PUBLIC_KEY_B64 = base64.b64encode(
    EVOLUTION_GRANT_PRIVATE_KEY.public_key().public_bytes(
        Encoding.DER, PublicFormat.SubjectPublicKeyInfo
    )
).decode()


def llm_snapshot() -> dict:
    """返回可安全修改的有效快照副本。"""
    return copy.deepcopy(VALID_LLM_SNAPSHOT)


def approval_token(
    *,
    subject: str,
    operation_id: str,
    request_digest: str,
    digest: str = VALID_LLM_SNAPSHOT["config_digest"],
) -> str:
    """签发与 orchestration 相同 scope 的 Ed25519 grant。"""
    now = int(time.time())
    return jwt.encode(
        {
            "sub": subject,
            "token_use": "evolution_credential",
            "grant_purpose": "e1_run",
            "aud": ["inalpha-evolver", "inalpha-dashboard-credential"],
            "jti": "11111111-1111-4111-8111-111111111111",
            "operation_id": operation_id,
            "config_id": VALID_LLM_SNAPSHOT["config_id"],
            "provider": VALID_LLM_SNAPSHOT["provider"],
            "llm_config_digest": digest,
            "request_digest": request_digest,
            "iat": now,
            "exp": now + 30 * 60 * 60,
        },
        EVOLUTION_GRANT_PRIVATE_KEY,
        algorithm="EdDSA",
    )
