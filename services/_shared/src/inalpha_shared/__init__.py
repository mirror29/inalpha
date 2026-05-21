"""Inalpha 各 service 共享的 FastAPI 基础设施。

公共能力（每个 service 直接 import 用）：

- `config.Settings`     基础环境变量 → settings，可继承
- `db`                  psycopg 异步连接池 + FastAPI dependency
- `errors`              统一错误码 + HTTP 异常类
- `auth`                JWT 验证 + `get_current_user` dependency
- `logging`             structlog JSON 配置 + trace_id 上下文
- `middleware`          请求日志 / 统一错误响应
"""
from .auth import User, get_current_user, verify_jwt
from .config import Settings, get_settings
from .db import DBConn, close_pool, get_conn, get_db, init_pool
from .errors import (
    ConflictError,
    ForbiddenError,
    NotFoundError,
    InalphaError,
    RateLimitedError,
    UnauthorizedError,
    ValidationError,
)
from .logging import configure_logging, get_logger
from .middleware import install_error_handler, install_request_logging

__all__ = [
    "ConflictError",
    "DBConn",
    "ForbiddenError",
    "NotFoundError",
    "InalphaError",
    "RateLimitedError",
    "Settings",
    "UnauthorizedError",
    "User",
    "ValidationError",
    "close_pool",
    "configure_logging",
    "get_conn",
    "get_current_user",
    "get_db",
    "get_logger",
    "get_settings",
    "init_pool",
    "install_error_handler",
    "install_request_logging",
    "verify_jwt",
]

__version__ = "0.1.0"
