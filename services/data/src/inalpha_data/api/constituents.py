"""``/constituents`` —— 指数成分 PIT 快照 + time-travel（#106 / ADR-0053 阶段 C）。

- ``POST /constituents/snapshot``:拉某指数**当前**成分（akshare）落库 ``as_of_date=今天``——
  从今天起向前累积 PIT 史（免费历史成分拿不到的现实下的唯一路径）。
- ``GET /constituents``:time-travel，返回 ``as_of_date <= as_of`` 的最近一份快照;无则
  ``is_pit=false`` 显式降级（§3.1，不静默假装 PIT）。

横截面选股/轮动回测的存活者偏差前提:每期取 as_of 那刻的真实成分,而非"今天还在的票"回看。
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from inalpha_shared import get_logger
from inalpha_shared.auth import User, get_current_user
from inalpha_shared.db import DBConn
from inalpha_shared.errors import InalphaError, ValidationError

from ..connectors.akshare import get_connector as get_akshare_connector
from ..schemas import (
    ConstituentItem,
    ConstituentsResponse,
    SnapshotConstituentsRequest,
    SnapshotConstituentsResponse,
)
from ..storage import constituents as store

_logger = get_logger(__name__)
router = APIRouter(tags=["constituents"])


class ConstituentsUnavailableError(InalphaError):
    code = "CONSTITUENTS_UNAVAILABLE"
    status_code = 502


@router.post("/constituents/snapshot", response_model=SnapshotConstituentsResponse)
async def snapshot_constituents(
    req: SnapshotConstituentsRequest,
    db: DBConn,
    _user: Annotated[User, Depends(get_current_user)],
) -> SnapshotConstituentsResponse:
    """拉 ``index_code`` 当前成分（akshare）落库，``as_of_date=今天``。

    每日（或按需）调用一次即向前累积一份 PIT 快照。akshare 只回当前成分，故本接口是
    PIT 史的**唯一来源**;源站失败 → 502，不静默写空（§3.1）。
    """
    try:
        conn = get_akshare_connector()
    except RuntimeError as exc:  # akshare connector 未注册（启动未 init）
        raise ConstituentsUnavailableError(
            f"akshare connector unavailable: {exc}", code="CONSTITUENTS_UNAVAILABLE"
        ) from exc

    members = await conn.fetch_index_constituents(req.index_code)
    if not members:
        raise ConstituentsUnavailableError(
            f"no constituents fetched for index {req.index_code!r} "
            "(akshare 拉取失败 / 不支持该指数)",
            details={"index_code": req.index_code},
        )

    today = datetime.now(UTC).date()
    async with db.transaction():
        n = await store.upsert_snapshot(
            db, index_code=req.index_code, as_of_date=today, constituents=members
        )
    _logger.info("constituent_snapshot_recorded", index_code=req.index_code, count=n)
    return SnapshotConstituentsResponse(
        index_code=req.index_code, as_of_date=today.isoformat(), count=n
    )


@router.get("/constituents", response_model=ConstituentsResponse)
async def get_constituents_pit(
    db: DBConn,
    _user: Annotated[User, Depends(get_current_user)],
    index_code: Annotated[str, Query(description="指数代码，如 000300")],
    as_of: Annotated[
        str | None,
        Query(description="PIT 时点 ISO 日期/时刻（只取 <= 它的最近快照）；省略=今天"),
    ] = None,
) -> ConstituentsResponse:
    """time-travel 查指数成分:返回 ``as_of_date <= as_of`` 的最近一份快照。

    早于最早快照（向前累积尚未覆盖该时点）→ ``is_pit=false`` + 空成分 + reason，**不**静默
    回退到"当前成分"假装 PIT（那正是存活者偏差，§3.1 拿不到时显式说明）。
    """
    if as_of is not None:
        try:
            as_of_date = datetime.fromisoformat(as_of.replace("Z", "+00:00")).date()
        except ValueError:
            raise ValidationError(
                f"invalid as_of {as_of!r}: expect ISO date/datetime", code="INVALID_AS_OF"
            ) from None
    else:
        as_of_date = datetime.now(UTC).date()

    snap_date, members = await store.get_constituents(
        db, index_code=index_code, as_of=as_of_date
    )
    return ConstituentsResponse(
        index_code=index_code,
        as_of=as_of_date.isoformat(),
        snapshot_date=snap_date.isoformat() if snap_date else None,
        is_pit=snap_date is not None,
        reason=None
        if snap_date is not None
        else (
            f"no constituent snapshot at or before {as_of_date.isoformat()} for "
            f"{index_code!r} — PIT coverage accumulates forward from first snapshot; "
            "treat as non-PIT (survivorship bias), do not use for unbiased backtest"
        ),
        constituents=[ConstituentItem(**m) for m in members],
    )
