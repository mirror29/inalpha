"""structlog 配置 —— JSON 输出 + ``trace_id`` 通过 contextvars 注入。"""
from __future__ import annotations

import logging
import sys

import structlog


def configure_logging(level: str = "INFO", service_name: str = "unknown") -> None:
    """所有 service 启动时调一次。

    输出全 JSON（一行一条），便于 ``docker logs`` + ELK / Loki / Grafana Tempo 解析。
    """
    log_level = getattr(logging, level.upper(), logging.INFO)
    logging.basicConfig(
        level=log_level,
        stream=sys.stdout,
        format="%(message)s",
    )

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.dict_tracebacks,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        cache_logger_on_first_use=True,
    )

    structlog.contextvars.bind_contextvars(service=service_name)


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """获取 structlog logger。"""
    return structlog.get_logger(name)  # type: ignore[no-any-return]
