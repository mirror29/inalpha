"""服务级配置 —— 从环境变量 / `.env` 文件加载。

每个 service 可继承 `Settings` 加自己的字段：

    from pydantic import Field
    from inalpha_shared.config import Settings

    class DataServiceSettings(Settings):
        binance_api_key: str = Field(..., alias="BINANCE_API_KEY")
        binance_api_secret: str = Field(..., alias="BINANCE_API_SECRET")
"""
from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """所有 service 共用的基础 settings。"""

    model_config = SettingsConfigDict(
        env_file=".env",
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
