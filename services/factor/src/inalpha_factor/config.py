"""factor service 专属 settings。

继承 ``inalpha_shared.Settings``，加 data-service URL + qlib 开关。
"""
from __future__ import annotations

from functools import lru_cache

from inalpha_shared.config import Settings as BaseSettings
from pydantic import Field


class FactorSettings(BaseSettings):
    """factor service 完整 settings。"""

    service_name: str = Field(default="factor", alias="SERVICE_NAME")

    data_service_url: str = Field(
        default="http://localhost:8001",
        alias="DATA_SERVICE_URL",
        description="data-service base URL，因子计算从这里拉 OHLCV。",
    )

    factor_service_port: int = Field(
        default=8004,
        alias="FACTOR_SERVICE_PORT",
        description="factor service 端口。8001 data / 8002 paper / 8003 research / 8004 factor。",
    )

    qlib_enabled: bool = Field(
        default=True,
        alias="FACTOR_QLIB_ENABLED",
        description="是否启用 qlib Alpha158 风格适配器。默认 True —— 因子是纯 pandas"
        "公式本地算，不依赖 pyqlib（ADR-0043 D1）。开关保留作降级阀门；关闭时 catalog"
        "里 qlib 因子标 available=false，pandas-ta + Alpha101 仍可用。",
    )

    snapshot_corr_threshold: float = Field(
        default=0.85,
        ge=0.5,
        le=1.0,
        alias="FACTOR_SNAPSHOT_CORR_THRESHOLD",
        description="snapshot top-N 去相关阈值：候选因子与已选因子时序 |spearman| ≥ 此值"
        "则跳过（ADR-0043 D3）。1.0 = 关闭去相关。",
    )

    snapshot_top_n: int = Field(
        default=10,
        ge=1,
        le=50,
        alias="FACTOR_SNAPSHOT_TOP_N",
        description="/snapshot 返回的有效因子数上限（按 |rank_ic| 排序）。控制喂给 LLM 的 token。",
    )

    min_effective_samples: int = Field(
        default=120,
        ge=30,
        alias="FACTOR_MIN_EFFECTIVE_SAMPLES",
        description="算有效性所需的最少（因子值, 前瞻收益）对。低于此标 low_confidence，"
        "不参与 top-N 排序。120 ≈ 单标的几个月日频，足以给方向性参考（非严谨学术 IC）。",
    )


@lru_cache(maxsize=1)
def get_factor_settings() -> FactorSettings:
    return FactorSettings()  # type: ignore[call-arg]
