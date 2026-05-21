"""测试错误码类生成的 HTTPException 格式。"""
from __future__ import annotations

import pytest

from inalpha_shared.errors import (
    ConflictError,
    ForbiddenError,
    NotFoundError,
    InalphaError,
    RateLimitedError,
    UnauthorizedError,
    ValidationError,
)


def test_inalpha_error_default() -> None:
    err = InalphaError("something broke")
    assert err.status_code == 500
    assert err.detail == {
        "code": "INTERNAL_ERROR",
        "message": "something broke",
        "details": {},
    }


def test_inalpha_error_override() -> None:
    err = InalphaError(
        "nope",
        code="CUSTOM_CODE",
        status_code=418,
        details={"x": 1, "y": "z"},
    )
    assert err.status_code == 418
    assert err.detail["code"] == "CUSTOM_CODE"
    assert err.detail["message"] == "nope"
    assert err.detail["details"] == {"x": 1, "y": "z"}


@pytest.mark.parametrize(
    ("cls", "code", "status"),
    [
        (NotFoundError, "NOT_FOUND", 404),
        (ValidationError, "VALIDATION_ERROR", 400),
        (UnauthorizedError, "UNAUTHORIZED", 401),
        (ForbiddenError, "FORBIDDEN", 403),
        (ConflictError, "CONFLICT", 409),
        (RateLimitedError, "RATE_LIMITED", 429),
    ],
)
def test_subclass_defaults(cls: type[InalphaError], code: str, status: int) -> None:
    err = cls("test message")
    assert err.status_code == status
    assert err.detail["code"] == code
    assert err.detail["message"] == "test message"


def test_subclass_can_override_code() -> None:
    err = UnauthorizedError("token expired", code="TOKEN_EXPIRED")
    assert err.status_code == 401
    assert err.detail["code"] == "TOKEN_EXPIRED"
