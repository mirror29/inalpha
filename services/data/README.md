# services/data

行情数据接入 + 时序存储 + 历史查询。

## 当前能力（D-3 起步）

| Endpoint | 用途 |
|---|---|
| `GET /health` | 存活探活 + DB ping，无 auth |
| `GET /bars` | 从 TimescaleDB 查 K 线，需 JWT |
| `POST /backfill/bars` | 从 Binance 拉历史 K 线落库，需 JWT |

后续（D-3+）：WebSocket `/ticks/{symbol}` 实时 quote 推送。

## 开发

前置：先把 `infra/` 的 docker compose + alembic migration 跑起来。

```bash
cd services/data
cp .env.example .env       # 至少改 DATABASE_URL（如果本机 postgres 不在 5433）
uv sync --group dev
uv run pytest              # 25 个左右测试
uv run uvicorn inalpha_data.main:app --reload --port 8001
```

然后另开终端测：

```bash
# 健康检查
curl http://localhost:8001/health

# 回填最近 7 天的 BTC/USDT 1 小时 K 线
JWT="$(...)"  # 后续 D-7 起前端会自动管 token；现在手动签个测试 token
curl -X POST http://localhost:8001/backfill/bars \
  -H "Authorization: Bearer $JWT" \
  -H "Content-Type: application/json" \
  -d '{
    "venue": "binance",
    "symbol": "BTC/USDT",
    "timeframe": "1h",
    "from_ts": "2026-05-14T00:00:00Z",
    "to_ts": "2026-05-21T00:00:00Z"
  }'

# 查 K 线
curl -G "http://localhost:8001/bars" \
  -H "Authorization: Bearer $JWT" \
  --data-urlencode "symbol=BTC/USDT" \
  --data-urlencode "from_ts=2026-05-14T00:00:00Z" \
  --data-urlencode "to_ts=2026-05-21T00:00:00Z"
```

## 架构

- `connectors/binance.py` —— CCXT async 包装，公开接口（OHLCV）免 key
- `storage/bars.py` —— bars 表读写，psycopg 异步，ON CONFLICT 幂等
- `api/{health,bars,backfill}.py` —— FastAPI 路由
- 全部 middleware（请求日志 / 错误处理 / JWT 验签）走 `inalpha_shared`

## 质量门

```bash
uv run pytest           # 单元 + 集成（集成要 docker 起着）
uv run pytest -m "not integration"   # 跳过集成
uv run ruff check
uv run mypy src
```
