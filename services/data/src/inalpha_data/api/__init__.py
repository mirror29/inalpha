"""FastAPI 路由 —— 在 main.py 里聚合挂载。"""
from . import backfill, bars, health, ticker

__all__ = ["backfill", "bars", "health", "ticker"]
