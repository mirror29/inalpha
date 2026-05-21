"""请求日志 / 错误处理 / ``trace_id`` middleware。

用法（在 service ``main.py`` 里）::

    from inalpha_shared import (
        configure_logging,
        install_request_logging,
        install_error_handler,
    )

    configure_logging(level=settings.log_level, service_name="data")
    app = FastAPI()
    install_request_logging(app)
    install_error_handler(app)
"""
from __future__ import annotations

import time
import uuid
from typing import Any

import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import HTTPException, RequestValidationError
from fastapi.responses import JSONResponse

from .errors import InalphaError

_logger = structlog.get_logger(__name__)


def install_request_logging(app: FastAPI) -> None:
    """每个请求记一条 JSON log，自动注入 ``trace_id``。

    - 入：读 ``X-Trace-Id`` 头；没有就生成 UUID
    - 出：把 trace_id 回写到响应头，便于客户端关联
    - 异常路径：用 ``logger.exception`` 写堆栈
    """

    @app.middleware("http")
    async def log_requests(request: Request, call_next: Any) -> Any:
        trace_id = request.headers.get("x-trace-id") or str(uuid.uuid4())
        start = time.monotonic()

        structlog.contextvars.bind_contextvars(trace_id=trace_id)

        try:
            response = await call_next(request)
        except Exception:
            duration_ms = (time.monotonic() - start) * 1000
            _logger.exception(
                "request_failed",
                method=request.method,
                path=request.url.path,
                duration_ms=round(duration_ms, 2),
            )
            structlog.contextvars.clear_contextvars()
            raise

        duration_ms = (time.monotonic() - start) * 1000
        _logger.info(
            "request",
            method=request.method,
            path=request.url.path,
            status=response.status_code,
            duration_ms=round(duration_ms, 2),
        )
        response.headers["x-trace-id"] = trace_id
        structlog.contextvars.clear_contextvars()
        return response


def install_error_handler(app: FastAPI) -> None:
    """统一错误响应格式 ``{code, message, details}``。

    处理 4 类异常：

    - :class:`InalphaError`：直接用其 ``detail``（已是统一格式）
    - :class:`RequestValidationError`：FastAPI 请求体 schema 校验，包成 ``VALIDATION_ERROR``
    - :class:`HTTPException`：FastAPI 内置异常，包成 ``HTTP_ERROR``
    - 未捕获 ``Exception``：兜底 ``INTERNAL_ERROR`` 500
    """

    @app.exception_handler(InalphaError)
    async def handle_inalpha_error(request: Request, exc: InalphaError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content=exc.detail)

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=400,
            content={
                "code": "VALIDATION_ERROR",
                "message": "request body validation failed",
                "details": {"errors": exc.errors()},
            },
        )

    @app.exception_handler(HTTPException)
    async def handle_http_exception(request: Request, exc: HTTPException) -> JSONResponse:
        content: dict[str, Any]
        if isinstance(exc.detail, dict):
            content = exc.detail
        else:
            content = {"code": "HTTP_ERROR", "message": str(exc.detail), "details": {}}
        return JSONResponse(status_code=exc.status_code, content=content)

    @app.exception_handler(Exception)
    async def handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
        _logger.exception("unhandled_exception", error_type=type(exc).__name__)
        return JSONResponse(
            status_code=500,
            content={
                "code": "INTERNAL_ERROR",
                "message": "internal server error",
                "details": {},
            },
        )
