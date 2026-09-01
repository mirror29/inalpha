"""Evolver API 路由集合。"""

from fastapi import APIRouter

from .campaign_routes import router as campaign_router
from .detail_routes import router as detail_router
from .run_routes import router as run_router

router = APIRouter(prefix="/api/v1", tags=["evolution"])
router.include_router(run_router)
router.include_router(detail_router)
router.include_router(campaign_router)
