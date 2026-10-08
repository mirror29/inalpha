# Inalpha Research · 多视角研究与三方辩论

`services/research` 是 FastAPI 研究服务（`:8003`）。`POST /deep_dive` 先并行运行核心
analysts，再在观点存在分歧时触发 bull / bear / risk 辩论，最后由 manager 生成结构化、
可回放的 `ResearchPlan`。

## 当前链路

```text
DeepDiveRequest
  → 预取 bars + factor snapshot
  → technical / fundamental / sentiment / risk / macro / valuation 并行
  → 可选 Buffett / Lynch / Wood / Burry / Druckenmiller / Marks 人格
  → 有分歧时 bull → bear → risk，支持软早停与总超时
  → manager 综合 briefs + debate log
  → ResearchPlan（factors / signals / strategy_hint / trigger / stop_reason）
```

- 单个 analyst 失败不会抹掉其他视角，失败 brief 会明确标记后交给 manager 综合。
- `as_of` 是严格研究截止点；返回值保留 briefs、辩论轮次、触发与停止原因供审计。
- 数据来自 `services/data`，当前有效因子来自 `services/factor`；JWT 沿调用链透传。
- 当前 research service 读取部署级 `LLM_PROVIDER` / `LLM_MODEL` 与对应 provider key；
  per-owner Dashboard key 尚未透传到本服务，部署者需把这一限制视为当前多租户边界。

## HTTP API

| 方法 | 路径 | 用途 |
|---|---|---|
| `GET` | `/health` | 服务与 provider 探活 |
| `POST` | `/deep_dive` | 执行完整研究链路，返回 `ResearchPlan` |

## 本地开发

```bash
cd services/research
uv sync --group dev
uv run uvicorn inalpha_research.main:app --reload --port 8003

uv run ruff check .
uv run pytest                       # 默认 fake LLM，不产生调用费用
```

真实模型测试会产生费用，只应在显式设置 provider/key 并主动运行 integration 标记时执行。

## 事件事实抽取

`POST /event-facts/extract` 通过 Data 服务读取归档并追加事实，要求用途为
`event_extract_request` 的 Research service token；这条确定性链路不调用 LLM。
后台 worker 由 `EVENT_EXTRACTION_ENABLED` 控制，启用前应先核对来源与覆盖。

`deterministic-event-extractor-v2` 保留结构化币种字段，补充 Bitcoin、Ethereum、Solana、
Cardano、Dogecoin、Polkadot、Chainlink 与 Binance Coin 的全称识别；文本中的 ticker
要求明确大写，避免将普通 `link` / `dot` 误认成资产。Bitcoin Cash / SV / Gold 与
Ethereum Classic 不映射到 BTC / ETH。资产被提及只表示报道关联，不代表事件发生在该资产上。

- 新版本用于新归档，不覆盖已有事实或不可变快照。旧事实修订仍须满足 Data 的版本与
  `available_at` 约束；不能将重新抽取的结果回填成过去已知的信息。
- 新闻策略 `first-seen-only-v1` 使用实际接收时间；发布时间不是系统当时已知的证据。
- 真实来源验证要按 `(source, source_event_id)` 去重，统计原文版本数与独立原文数；开发库
  的测试事实不能用于证明真实事件覆盖。快照 `coverage.complete` 也不代表统计样本充分。
- 下一步与实际验证记录集中维护在 [当前状态文档](../../docs/04-current-state.md#未完成--下一步)。

## 有界归档事件验证

对明确选出的真实新闻归档，先预览抽取结果，再选择是否写入事实账本：

```bash
uv run python scripts/extract_archived_events.py --raw-event-id <raw-event-uuid>
uv run python scripts/extract_archived_events.py --raw-event-id <raw-event-uuid> --write
```

可重复传入 `--raw-event-id`，最多 100 个不同 ID。脚本仅接受 `coindesk` / `kraken_blog`
的 `selected-news-forward@1`、`first-seen-only-v1` 未撤回归档，经正常服务鉴权访问 Data。
确定性抽取不调用 LLM；重复写入由 Data 幂等处理，`processed` 不代表新增事实数。
保留 `accepted_at` 作为事实可见时间，不回填至新闻发布时间。预览及写入均汇总事件分类、
关联资产与失败类型；没有关联资产的事实不能直接视为可用于单资产评估的有效样本。

## 隔离 E2 输入刷新

已有真实归档的本地开发环境可以使用根目录 `scripts/replay-e2-archive.py`，
通过正常 Data API 将 CoinDesk/Kraken Blog 的最新独立原文同步到已有 E2 审计库。
仅允许 `selected-news-forward@1` / `first-seen-only-v1` 来源，测试 fixture 不进入真实覆盖。

前置条件：目标数据库以 `inalpha_e2_real_` 开头、已初始化并至少有一条 UUID 不存在于源库的原文用于路由校验；
独立 Data 服务连接该库，独立 Research 服务的 `DATA_SERVICE_URL` 指向它，
并设置 `EVENT_EXTRACTION_ENABLED=true`。源和目标使用同一开发 JWT 配置。数据库连接须明确指向本机，不能通过 URL 查询参数
覆盖连接目标；两个 Data 服务须使用不同端口，克隆源库中的相同原文 UUID 不能作为路由证明。
先核对服务健康与连接配置，根服务和其他任务的 worker 无需重启。

在仓库根目录执行（使用已安装 psycopg/httpx/PyJWT/python-dotenv 的 Python 环境）：

```bash
python scripts/replay-e2-archive.py \
  --source-env-file /path/to/local/.env \
  --destination-database inalpha_e2_real_YOUR_AUDIT_ID \
  --source-data-url http://127.0.0.1:8001 \
  --destination-data-url http://127.0.0.1:18011 \
  --output /path/to/local/replay-receipt.json
```

命令只读源库并通过 service token 写目标原文，不直接插事实、不调用 LLM；
现有 Research worker 领取 durable extraction jobs，写入事实并完成任务。
相同内容重复运行复用原文版本。源身份/hash/首次观察时间必须一致；
正常 Data 内容修订会把本地接收与获取时间保守后移，工具明确计数这类修订，
不绕过版本规则或回填旧可见时间。网络或时间校验失败可能已写入部分原文，
修复后可重新执行；最终成功回执的 created 数量仅代表该次执行，不代表此前部分写入。

确认任务完成后，用 `scripts/check-e2-readiness.py --env-file <目标库的本地环境文件>`
核对选择窗口；默认 BTC 永续 4h，`--timeframe 1h` 可检查一小时行情。
就绪检查统计与 HypothesisSpec 相同的支持类型，包括 confirmed / hybrid 事件，
并单列四类直接触发事件；质量门槛和 24 小时独立性规则不变。
就绪检查只证明必要输入覆盖，匹配对照、FDR、收益和 Forward 仍需正式链路验证。
模型预算、当前真实证据与下一步清单以 [当前状态文档](../../docs/04-current-state.md#未完成--下一步) 为准。
