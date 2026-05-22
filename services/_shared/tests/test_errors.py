"""测试错误码类生成的 HTTPException 格式。"""
from __future__ import annotations

import pytest

from inalpha_shared.errors import (
    ConflictError,
    ForbiddenError,
    InalphaError,
    NotFoundError,
    RateLimitedError,
    UnauthorizedError,
    ValidationError,
)


def test_inalpha_error_default() -> None:
    err = InalphaError("something broke")
    assert err.status_code == 500
    # self.code 也得是默认值（D-8b' review 修：构造器要写 self.code）
    assert err.code == "INTERNAL_ERROR"
    assert err.message == "something broke"
    assert err.details == {}
    assert err.detail == {
        "code": "INTERNAL_ERROR",
        "message": "something broke",
        "details": {},
    }


def test_inalpha_error_override_writes_to_instance() -> None:
    """构造器传 code / status_code 必须写到 self，否则 except 路径拿不到。"""
    err = InalphaError(
        "nope",
        code="CUSTOM_CODE",
        status_code=418,
        details={"x": 1, "y": "z"},
    )
    # detail dict（HTTP body 用）
    assert err.detail["code"] == "CUSTOM_CODE"
    assert err.detail["message"] == "nope"
    assert err.detail["details"] == {"x": 1, "y": "z"}
    # self attr（Python except 路径用）—— 这是 D-8b' review 修复的核心
    assert err.code == "CUSTOM_CODE"
    assert err.status_code == 418
    assert err.message == "nope"
    assert err.details == {"x": 1, "y": "z"}


def test_caller_can_inspect_code_via_except() -> None:
    """实战路径：except InalphaError as e → e.code 拿到运行时 code，不是父类默认。"""
    try:
        raise InalphaError("x", code="MY_RUNTIME_CODE")
    except InalphaError as e:
        assert e.code == "MY_RUNTIME_CODE"


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
    # self.code 同样要被写到（review 修复）
    assert err.code == "TOKEN_EXPIRED"
