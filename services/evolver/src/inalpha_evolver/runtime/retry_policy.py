"""One transient-failure classification for both durable research dispatchers."""

import httpx

from ..owner_llm import CredentialTemporarilyUnavailable


def retryable_failure(error: Exception) -> bool:
    """Retry dependency outages, not invalid research, authorization, or budget exhaustion."""
    return (
        isinstance(error, CredentialTemporarilyUnavailable)
        or getattr(error, "code", None) in {
            "EVOLUTION_DATA_FRESHNESS_FAILED", "EVOLUTION_DATA_UNREACHABLE",
        }
        or isinstance(error, (httpx.TimeoutException, httpx.NetworkError))
        or (
            isinstance(error, httpx.HTTPStatusError)
            and (error.response.status_code >= 500 or error.response.status_code in {408, 429})
        )
    )


def primary_exception(error: Exception) -> Exception:
    """Unwrap TaskGroup failures while preserving domain error codes."""
    current = error
    while isinstance(current, BaseExceptionGroup):
        nested = [item for item in current.exceptions if isinstance(item, Exception)]
        if not nested:
            return error
        current = nested[0]
    return current
