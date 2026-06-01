"""FastAPI 路由 —— 在 main.py 里聚合挂载。"""
from . import backfill, bars, fundamentals, health, news, ticker

__all__ = ["backfill", "bars", "fundamentals", "health", "news", "ticker"]
