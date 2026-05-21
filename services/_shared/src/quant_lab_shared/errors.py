"""统一错误码与 HTTP 异常类。

约定的响应 body 格式（参考 ADR-0002）::

    { "code": "STRATEGY_NOT_FOUND", "message": "...", "details": {...} }

子类化 ``QuantLabError`` 自定义业务错误，``code`` / ``status_code`` 当类属性覆盖即可。
``raise`` 后会被 ``install_error_handler`` 转成标准 JSON 响应。
"""
from __future__ import annotations

from typing import Any

from fastapi import HTTPException


class QuantLabError(HTTPException):
    """业务错误基类。"""

    code: str = "INTERNAL_ERROR"
    status_code: int = 500

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        status_code: int | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(
            status_code=status_code or self.status_code,
            detail={
                "code": code or self.code,
                "message": message,
                "details": details or {},
            },
        )


class NotFoundError(QuantLabError):
    """资源不存在。"""

    code = "NOT_FOUND"
    status_code = 404


class ValidationError(QuantLabError):
    """业务校验失败（不是 FastAPI 请求体 schema 校验）。"""

    code = "VALIDATION_ERROR"
    status_code = 400


class UnauthorizedError(QuantLabError):
    """未认证：缺 token / token 无效 / 过期。"""

    code = "UNAUTHORIZED"
    status_code = 401


class ForbiddenError(QuantLabError):
    """已认证但无权限。"""

    code = "FORBIDDEN"
    status_code = 403


class ConflictError(QuantLabError):
    """状态冲突（如重复创建、并发修改、状态机不允许转换）。"""

    code = "CONFLICT"
    status_code = 409


class RateLimitedError(QuantLabError):
    """限流。"""

    code = "RATE_LIMITED"
    status_code = 429
