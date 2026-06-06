"""Risk API（ADR-0006 Slice 7）—— agent / UI 自检 + 人工解锁入口。

4 个端点：

- `GET  /risk/rules`           —— describe 当前配置的 rules（启动时加载 TOML）
- `GET  /risk/locks`           —— 列 PostgreSQL ``risk_locks`` 中 **当前生效** 的锁
- `GET  /risk/locks/history`   —— 列**最近**风控锁（含已过期 / 已解锁），按 locked_at DESC
- `POST /risk/locks/{id}/unlock` —— 人工解锁（**人工操作，不让 LLM 调**）

注：live runner 走 ``RiskGuard``（HTTP 异步路径）**直接把锁写进 PG ``risk_locks``**，
所以 ``/locks`` / ``/locks/history`` 是真实数据；backtest 路径走 ``InMemoryLockStore`` +
``LockStoreReconciler``（dump 进同一张表）。``/locks`` 只看 active，故短时效锁过期后
看不到——``/locks/history`` 补这道「事后可查」的审计视图。

[ADR-0006 §D6](../../../../docs/miro/decisions/0006-risk-rules.md) MCP tool 设计。
Mastra TS 侧 MCP wrapping 在 ``packages/orchestration``，本服务只暴露 HTTP 路由。
"""
from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from inalpha_shared.auth import User, get_current_user
from inalpha_shared.db import DBConn
from pydantic import BaseModel, Field

from ..execution.risk_rules import (
    ClosedTradeRecord,
    RiskRulesConfig,
    build_rules,
    load_risk_rules_config,
)
from ..storage import risk_locks as locks_store

router = APIRouter(prefix="/risk", tags=["risk"])


# ─── 启动时配置加载（模块级缓存） ───


_config_cache: RiskRulesConfig | None = None


def _resolve_config_path() -> Path | None:
    """优先 env `RISK_RULES_CONFIG`，否则 `services/paper/configs/risk_rules.toml`。"""
    env_path = os.environ.get("RISK_RULES_CONFIG")
    if env_path:
        path = Path(env_path)
        return path if path.exists() else None
    # 默认相对路径（从 service 根目录运行时）
    candidate = Path("configs/risk_rules.toml")
    if candidate.exists():
        return candidate
    # fallback：相对本文件
    fallback = Path(__file__).resolve().parent.parent.parent.parent / "configs" / "risk_rules.toml"
    return fallback if fallback.exists() else None


def _get_config() -> RiskRulesConfig:
    """加载 + 缓存。测试时用 `_reset_config_cache()` 重置。"""
    global _config_cache
    if _config_cache is None:
        path = _resolve_config_path()
        _config_cache = load_risk_rules_config(path) if path else RiskRulesConfig()
    return _config_cache


def _reset_config_cache() -> None:
    """测试 hook。"""
    global _config_cache
    _config_cache = None


# ─── 用于 build_rules short_desc 渲染的最小依赖 ───


class _NoopRepo:
    def get_closed_trades(self, **_: object) -> list[ClosedTradeRecord]:
        return []


class _NoopCalendar:
    def is_trading_hours(self, *_: object, **__: object) -> bool:
        return True

    def next_session_open(self, *_: object, **__: object) -> datetime:
        return datetime.now(UTC)


# ─── Pydantic response 模型 ───


class RuleDescription(BaseModel):
    name: str
    short_desc: str


class RulesListResponse(BaseModel):
    enabled: bool
    starting_balance: float
    rules: list[RuleDescription]


class LockResponse(BaseModel):
    id: int
    scope: str
    market: str | None
    symbol: str | None
    side: str
    rule_name: str
    reason: str
    locked_at: datetime
    locked_until: datetime


class LocksListResponse(BaseModel):
    locks: list[LockResponse]


class RecentLockResponse(LockResponse):
    """``/risk/locks/history`` 一行——比 active 锁多带解锁/状态元数据。"""

    active: bool
    unlocked_at: datetime | None
    unlocked_by: str | None
    unlock_reason: str | None


class RecentLocksListResponse(BaseModel):
    locks: list[RecentLockResponse]


class UnlockRequest(BaseModel):
    reason: str = Field(..., min_length=1)


# ─── Routes ───


@router.get("/rules", response_model=RulesListResponse)
async def list_rules(
    _user: Annotated[User, Depends(get_current_user)],
) -> RulesListResponse:
    """describe 启动时加载的 rule 配置 + 渲染 `short_desc`。"""
    cfg = _get_config()
    descriptions: list[RuleDescription] = []
    if cfg.enabled and cfg.rules:
        rules = build_rules(
            cfg, trade_repo=_NoopRepo(), market_calendar=_NoopCalendar()
        )
        descriptions = [
            RuleDescription(name=r.name, short_desc=r.short_desc()) for r in rules
        ]
    return RulesListResponse(
        enabled=cfg.enabled,
        starting_balance=cfg.starting_balance,
        rules=descriptions,
    )


@router.get("/locks", response_model=LocksListResponse)
async def list_locks(
    _user: Annotated[User, Depends(get_current_user)],
    conn: DBConn,
    scope: str | None = None,
    market: str | None = None,
    symbol: str | None = None,
    limit: int = 100,
) -> LocksListResponse:
    """列 PostgreSQL `risk_locks` 中 `now` 仍生效的锁。"""
    rows: list[dict[str, Any]] = await locks_store.list_active(
        conn,
        now=datetime.now(UTC),
        scope=scope,
        market=market,
        symbol=symbol,
        limit=limit,
    )
    return LocksListResponse(locks=[LockResponse.model_validate(r) for r in rows])


@router.get("/locks/history", response_model=RecentLocksListResponse)
async def list_locks_history(
    _user: Annotated[User, Depends(get_current_user)],
    conn: DBConn,
    limit: int = 50,
) -> RecentLocksListResponse:
    """列**最近**风控锁（含已过期 / 已解锁），按 ``locked_at`` DESC。

    风控锁多为短时效（如 CooldownRule 5min），``/locks`` 只看 active 会让「刚触发过
    风控」过期即不可查。本端点给 UI / 审计一个事后复盘的历史视图。
    """
    bounded = max(1, min(limit, 200))
    rows: list[dict[str, Any]] = await locks_store.list_recent(conn, limit=bounded)
    return RecentLocksListResponse(
        locks=[RecentLockResponse.model_validate(r) for r in rows]
    )


@router.post("/locks/{lock_id}/unlock")
async def unlock(
    lock_id: int,
    body: UnlockRequest,
    user: Annotated[User, Depends(get_current_user)],
    conn: DBConn,
) -> dict[str, bool]:
    """人工解锁。`unlocked_by = user.sub`，软删（active=FALSE）。"""
    ok = await locks_store.manual_unlock(
        conn, lock_id, unlocked_by=user.user_id, unlock_reason=body.reason
    )
    if not ok:
        raise HTTPException(
            status_code=404, detail=f"lock {lock_id} not found or already inactive"
        )
    return {"ok": True}
