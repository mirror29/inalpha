"""服务级配置 —— 从环境变量 / `.env` 文件加载。

每个 service 可继承 `Settings` 加自己的字段：

    from pydantic import Field
    from inalpha_shared.config import Settings

    class DataServiceSettings(Settings):
        binance_api_key: str = Field(..., alias="BINANCE_API_KEY")
        binance_api_secret: str = Field(..., alias="BINANCE_API_SECRET")

`.env` 文件加载顺序（pydantic-settings list 语义：后者覆盖前者）：

1. ``<repo-root>/.env`` —— **统一入口**，所有 service 共享一份配置
2. ``./.env``（cwd 下，通常是 service 目录） —— 兼容旧用户 services/*/.env
   作为 fallback；用户迁移完成后可删除
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# 仓库根 = 本文件 services/_shared/src/inalpha_shared/config.py 向上 4 层
_REPO_ROOT = Path(__file__).resolve().parents[4]


class Settings(BaseSettings):
    """所有 service 共用的基础 settings。"""

    model_config = SettingsConfigDict(
        env_file=(str(_REPO_ROOT / ".env"), ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = Field(..., alias="DATABASE_URL")
    """Postgres 连接串，形如 ``postgresql+psycopg://user:pass@host:port/db``。"""

    jwt_secret: str = Field(..., alias="JWT_SECRET")
    """JWT 签名密钥。MVP 走 HS256 共享密钥；多 service 共用同一个 secret。"""

    jwt_algorithm: str = Field(default="HS256", alias="JWT_ALGORITHM")
    """JWT 签名算法。MVP HS256 已够，Phase F+ 评估 RS256/JWKS。"""

    service_name: str = Field(default="unknown", alias="SERVICE_NAME")
    """本 service 的名称，用于日志 / metrics 标签。"""

    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    """日志级别：DEBUG / INFO / WARNING / ERROR。"""


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """全局 singleton settings。

    测试时用 FastAPI ``app.dependency_overrides[get_settings] = ...`` 替换。
    """
    return Settings()  # type: ignore[call-arg]  # pydantic-settings 从环境读字段
