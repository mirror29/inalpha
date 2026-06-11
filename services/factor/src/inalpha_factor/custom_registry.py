"""已注册自定义因子的进程内注册表（D-12 · 因子发现 L1）。

``status='registered'`` 即生产（无单独生产表）：本模块把 DB 里的 registered
表达式缓存成 ``(spec, parsed)`` 列表，:class:`adapters.custom_adapter.CustomAdapter`
同步读取——解决"adapter.compute 是同步、DB 是异步"的阻抗。

刷新时机：

- lifespan 启动加载一次 + 周期任务（60s）后台刷新
- review 端点 register/reject 后**立即** ``refresh()``（注册秒生效，不等周期）

DB 不可用时注册表为空列表——custom 源退化为"无因子"，timing/score/catalog 照常。
表达式在入注册表时重新过 ``parse_expression``（防御：绕过 API 直插 DB 的非法行
只会被跳过 + 留日志，不会让整个 catalog 挂掉）。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from inalpha_shared.db import get_conn

from .adapters.base import FactorSpec
from .expression import ExpressionError, ParsedExpression, parse_expression
from .storage import candidates as candidates_store

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class RegisteredFactor:
    spec: FactorSpec
    parsed: ParsedExpression


_registry: list[RegisteredFactor] = []


def get_registered() -> list[RegisteredFactor]:
    """当前已注册自定义因子（同步读缓存；空 = 无注册或 DB 不可用）。"""
    return list(_registry)


async def refresh() -> int:
    """从 DB 重载注册表；返回条数。失败保留旧缓存（log warning）。"""
    try:
        async with get_conn() as conn:
            rows = await candidates_store.list_registered(conn)
    except Exception as exc:
        logger.warning("custom factor registry refresh failed（保留旧缓存）: %r", exc)
        return len(_registry)

    fresh: list[RegisteredFactor] = []
    for row in rows:
        try:
            parsed = parse_expression(row["expression"])
        except ExpressionError as exc:
            logger.warning(
                "registered factor %s 表达式非法（跳过）: %s", row["id"], exc
            )
            continue
        fid = f"custom.{row['expression_hash']}"
        name = row.get("name") or (
            row["expression"] if len(row["expression"]) <= 60 else row["expression"][:57] + "..."
        )
        fresh.append(
            RegisteredFactor(
                spec=FactorSpec(
                    fid, "custom", name, "custom",
                    extras={"expression": row["expression"]},
                ),
                parsed=parsed,
            )
        )
    _registry[:] = fresh
    return len(fresh)


def clear() -> None:
    """测试用：清空注册表。"""
    _registry.clear()
