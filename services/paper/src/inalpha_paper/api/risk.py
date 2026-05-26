"""Risk API（ADR-0006 Slice 7）—— agent / UI 自检 + 人工解锁入口。

3 个端点：

- `GET  /risk/rules`           —— describe 当前配置的 rules（启动时加载 TOML）
- `GET  /risk/locks`           —— 列 PostgreSQL ``risk_locks`` 中 active 锁
- `POST /risk/locks/{id}/unlock` —— 人工解锁（**人工操作，不让 LLM 调**）

注：当前 PostgreSQL ``risk_locks`` 表是**只读视图**——RiskEngine 仍只写
`InMemoryLockStore`。InMemory → PostgreSQL 的 reconcile worker 独立 Slice 处理。
本 API 路由设计层面已就绪，等 reconcile 补完即真用。

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
