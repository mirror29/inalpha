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

## Unified usage and development pilot artifacts

Research provider attempts are recorded in the owner-scoped unified ledger (migration
0066 required), including retries and returned usage before JSON parsing. Unknown usage
or prices remain unknown. Production deep dives keep their existing response schema.
For offline evidence preparation and #206 adapter contracts, see
[Unified model usage](../../docs/usage/README.md). These tools do not authorize paid runs
or establish real-data pilot readiness.

## 旧版事件事实审核（已部署，helper→Data单条写入已验证）

`POST /event-facts/review` 沿用 `event_extract_request` 平台服务鉴权，不接受普通 owner
修改全局事实。请求指定原事实 ID/哈希、纠正 UUID 和审核理由；`dry_run` 默认 true，
读取对应的不可变原文并用 v3 规则重新分类，检查资产、行动与证据不变，不写库。

审核者检查预览结果后，以同一参数、`dry_run=false` 和返回的 `expected_review_hash`
提交。服务重新计算绑定调用者、原事实和审核内容的摘要；参数变化或漏摘要时拒绝。
同一文章已有新原文时，旧事实不能再确认为活动分类；显式撤回旧分类仍允许。
Data 原子追加撤回/替代版本，生成当前数据库可见时间，并保存服务身份审计。

没有批量自动纠正；若已有目标分类，需显式审核 `retract=true` 的新预览。原文撤回也只
允许撤回，不能生成活动替代事实。回执不完整或响应丢失时不自动重发；保留同一纠正 UUID
和预览参数，恢复后重核来源并由调用者显式重发，Data 返回同一审计结果。服务权限失效
应重新取得同一已验证服务身份的合法授权（正常Research令牌刷新保持 `service:research`），
改变服务身份或预览调用者会拒绝原请求；不能伪造 owner 或演化 grant。

v3 规则确认不等于语义审核、匹配对照或 FDR 通过，不能借此补齐旧冻结窗口。
状态及实测证据见 [当前状态](../../docs/04-current-state.md)。

2026-10-14初次部署回执：正常Research服务helper经Data服务鉴权完成一条真实原文预览，保留监管分类、
无写入；这不是认证入站HTTP或owner授权验收。详见 [发布回执](../../docs/validation/event-fact-review-deployment-2026-10-14.json)。

后续逐条预览31条旧监管事实，只在明确阅读原文后纠正一条SoFiUSD科普文章；同一冻结请求由第二个独立helper进程重发，复用原审计和替代事实。剩余30条仍未纠正，不能把v3规则输出当成完整语义审核或解除E2输入门槛。认证入站HTTP、完整服务恢复与正常owner搜索待验证。见 [单条生产回执](../../docs/validation/event-fact-single-production-2026-10-14.json)。

同轮后续对两条BTC旧监管事实明确检查归档后保留监管分类，各追加一个v3确认版本，无撤回；其中SEC事实仅有标题，不能宣称完整正文审核。确认后的owner预检仍待读取，不宣称质量守卫已解除。见 [确认回执](../../docs/validation/event-fact-btc-confirmation-2026-10-14.json)。
