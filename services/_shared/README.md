# services/\_shared

各 service 共享的 FastAPI 基础设施。**所有 `services/*/` 都依赖它**（通过 uv 本地路径依赖）。

提供的能力：

| 模块 | 用途 |
|---|---|
| `config.Settings` | 基础 settings（DATABASE_URL / JWT_SECRET / SERVICE_NAME / LOG_LEVEL），子类化加各 service 自己的字段 |
| `db.init_pool` / `close_pool` / `get_conn` / `DBConn` | psycopg 异步连接池 + FastAPI dependency |
| `errors.QuantLabError` + 6 个子类 | 统一错误码（NOT_FOUND / VALIDATION_ERROR / UNAUTHORIZED / ...），HTTP 异常会被 middleware 转成 `{code, message, details}` JSON |
| `auth.User` / `verify_jwt` / `get_current_user` | JWT HS256 验证 + FastAPI dependency 注入 User |
| `logging.configure_logging` / `get_logger` | structlog JSON 输出 + trace_id 上下文 |
| `middleware.install_request_logging` / `install_error_handler` | 请求日志 / 错误统一包装 |

## 用法（每个 service 的 `main.py` 几乎都长这样）

```python
from contextlib import asynccontextmanager

from fastapi import FastAPI
from quant_lab_shared import (
    Settings, configure_logging,
    init_pool, close_pool,
    install_request_logging, install_error_handler,
)

settings = Settings()  # 各 service 子类化加自己的字段
configure_logging(level=settings.log_level, service_name=settings.service_name)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_pool(settings.database_url)
    yield
    await close_pool()


app = FastAPI(lifespan=lifespan)
install_request_logging(app)
install_error_handler(app)


# 在路由里用 DBConn / User dependency
from typing import Annotated
from fastapi import Depends
from quant_lab_shared.auth import User, get_current_user
from quant_lab_shared.db import DBConn

@app.get("/strategies")
async def list_strategies(
    db: DBConn,
    user: Annotated[User, Depends(get_current_user)],
):
    async with db.cursor() as cur:
        await cur.execute(
            "SELECT id, name FROM strategies WHERE created_by = %s",
            (user.user_id,),
        )
        return await cur.fetchall()
```

## 开发

```bash
cd services/_shared
uv sync --group dev
uv run pytest                # 跑单元 + dependency 测试
uv run pytest -m integration # 跑需要 DB 的集成测试（先 docker compose up）
uv run mypy src
uv run ruff check src tests
```

## 当被其它 service 依赖

在 `services/<name>/pyproject.toml`：

```toml
[project]
dependencies = [
    "quant-lab-shared",
    # ...
]

[tool.uv.sources]
quant-lab-shared = { path = "../_shared", editable = true }
```

`editable = true` 让 `_shared` 修改实时生效，不用每次 `uv sync`。
