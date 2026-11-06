# Inalpha Evolver · E1/E2 策略演化闭环

`services/evolver` 是独立的 FastAPI 策略演化服务（`:8005`）。它冻结实验输入，调用当前
owner 授权的 LLM 生成 unified diff 或结构化假设，在受限加载器和独立回测子进程中评估候选，
并把 E1 run、E2 loop/campaign、候选、费用与可复现元数据持久化到 PostgreSQL。

## 安全与产品边界

- E1 计费 run 需要可信 UI 显式审批；E2 以 owner 点击或明确说“开始进化”作为整次 campaign
  的授权，不逐代审批。E1 与 E2 启动时验证签名授权，并使用绑定
  owner/operation/purpose/request digest 的短时 Ed25519 credential grant（最长 30 小时）。
  E2 loop 启动后持久化 owner-bound 授权、冻结模型和费用上限（授权有效期 90 天）；后续
  调用通过该授权签发短时凭据，不依赖最初聊天 turn 的凭据持续存活。幂等键标识同一操作。
- bars、数据 manifest/hash、种子源码、非密钥 LLM 配置和定价摘要在执行前冻结；baseline、
  seed 与候选使用同一份 frozen bars。
- 用户 LLM API key 只在执行时通过 Dashboard 内部路由按 owner/config_id 获取，不进入 run
  配置、候选记录或日志；队列只短暂保存 orchestration 签发的 capability，兑换后立即清除。
- 当前演化费用审批只为 `deepseek`、`openai`、`kimi`、`zhipu` 各自的默认模型维护冻结计价
  表；未知模型与其他 provider 均 fail closed。输入 UTF-8 字节数与输出 token 数也受冻结
  上限约束，因此 `budget × 最大单候选估算` 是执行硬边界。它们仍可用于普通对话。
- 演化运行只连接上述 provider 的官方 HTTPS API 端点；普通对话可用的自定义代理地址不会
  进入 Evolver，避免服务端凭据解析链路被用来访问内网地址。
- 所有 run/candidate 查询都按认证 owner 隔离；全局并发与单账户 active run 数均有限制。
- Evolver 只生成和评估候选，绝不会自动 promote、启动策略或下单。
- E2 在一个 owner-bound 操作授权内自动完成五代搜索、冠军锁定、Forward 和一次性 holdout；
  最终采用仍需用户手动执行，采用结果强制 `runner_eligible=false`。
- AST 审计、受限动态加载、契约检查和回测子进程是当前防线；子进程并非 hardened container
  或 VM，不应把未知代码当作已完成强隔离。

## 执行链路

```text
Dashboard / orchestration
  → 冻结 LLM + pricing snapshot，POST /api/v1/runs/preflight 校验种子与完整窗口
  → 将 preparation 源码/数据指纹绑定费用审批与签名摘要，取得逐次审批
  → 签发 owner/operation/config/digest 绑定的 Ed25519 credential grant
  → POST /api/v1/runs（Idempotency-Key + 两个审批/凭据 header）
  → 冻结真实 bars + manifest/hash，复核批准的 preparation 指纹
  → 解析 seed，跑同数据 baseline
  → LLM 生成 unified diff
  → diff 应用 → AST/loader/contract 校验 → 子进程回测
  → fitness 排序，持久化 token/cost/失败原因
  → 用户显式选择后，另走 paper promote / start / plan-exec
```

E2 自动闭环：

```text
策略 / 模拟盘 / E1 结果页的一句“开始进化”
  → 创建或复用 owner + target 唯一的 EvolutionLoop
  → 冻结 bars、事件 snapshot 与 60/20/20 数据切分
  → 五代 8 个假设 × 3 个确定性实现
  → 服务端锁定唯一冠军 → Paper 独立 Forward sandbox
  → Forward 通过后消费唯一 sealed holdout attempt
  → adoption_ready → 用户手动采用为不可运行的实验性策略资产
```

### 持久化恢复与验证状态（2026-09-30 核对）

- Loop baseline 与 campaign dispatcher 使用数据库租约、心跳和 fencing；过期 worker 不得
  写入结果。重启后重新领取可执行任务，复用冻结 snapshot、proposal checkpoint 与已落盘步骤。
- E1 baseline → E2 campaign 的交接在同事务内关联唯一 campaign 和 UI 状态事件；重复触发
  复用当前 owner + target 的活动 loop，恢复不得重新取得授权或突破原费用上限。
- 费用按调用先预留再结算，授权到期/撤销或预算不足会阻止继续产生计费调用。Forward 证据和一次性
  holdout 门禁仍由服务端管理，最终采用必须手动执行，候选保持 `runner_eligible=false`。
- 仓库已有恢复、预算、租约、五代和交接相关测试；真实运行与重启验证尚需记录证据。
  优先级统一见[当前状态文档](../../docs/04-current-state.md#未完成--下一步)。

### 审批命令恢复（2026-10-09 已部署，真实恢复验收待完成）

E1 的批准决定与冻结执行命令同事务保存。执行命令使用 AES-256-GCM 无损加密，
认证绑定 owner、operation、session、tool 与原输入摘要；普通审批历史独立脱敏。
恢复不重建参数、不延长授权，旧格式仍校验原摘要，不能猜测已脱敏丢失的参数。

加密密钥通过独立 HKDF 域从 orchestration 的 `JWT_SECRET` 派生。重启必须保持该配置稳定；
当前没有旧密钥读取能力，轮换会使旧密文无法恢复，应等待最长 24 小时批准恢复窗口结束。
已提交的运行不受影响。隔离库恢复检查已通过；编排、Evolver、Dashboard 已协调部署，
正常 owner 的旧运行查询与详情已验证，批准命令响应丢失及完整恢复仍待验收；
证据见[本地恢复回执](../../docs/validation/evolution-approval-encryption-local-2026-10-09.json)。

核心目录：

| 目录 | 职责 |
|---|---|
| `api/` | E1 run、E2 capability/loop/campaign/adoption API、审批与幂等校验 |
| `data/` | bars 拉取、质量校验、冻结文件、manifest 与 hash |
| `governor/` | E1 seed 解析和单代演化流程 |
| `hypothesis/` | E2 HypothesisSpec、lane DSL、确定性编译与行为 novelty |
| `mutator/` | owner-scoped LLM 客户端、prompt 与 unified-diff 应用 |
| `sandbox/` | 复用 paper 的 AST 审计、受限加载与 Strategy 契约检查 |
| `evaluator/` | frozen dataset 回测、子进程资源限制与 fitness |
| `runtime/` | 异步 dispatcher、slot 并发、取消、超时与终态收口 |
| `storage/` | PostgreSQL run/candidate 持久化与 owner-scoped 查询 |
| `owner_llm.py` | 转交短时、逐操作且同 scope 可重试的 credential grant，读取 owner 模型配置 |

## HTTP API

所有 `/api/v1/*` 端点都要求用户 JWT。

> `inalpha-evolver 0.2.0` 收紧了创建 run 的契约：`POST /api/v1/runs` 现在必须同时提供
> 冻结的 `llm` 快照、`Idempotency-Key` 与 `X-Evolution-Credential`。这是 0.x 阶段的安全性
> breaking change；自建部署应将 Dashboard、orchestration、migration 0041 与 Evolver
> 作为同一次升级发布，直接调用旧接口的客户端必须同步更新。

| 方法 | 路径 | 说明 |
|---|---|---|
| `POST` | `/api/v1/runs/preflight` | 准备 owner 的种子与完整行情窗口，返回 manifest、审批摘要和估算；不创建 run、不兑换模型凭据 |
| `POST` | `/api/v1/runs` | 创建或幂等复用 run，返回 `202`；额外要求审批与幂等 header |
| `GET` | `/api/v1/runs` | 分页列出当前 owner 的 run |
| `GET` | `/api/v1/runs/{run_id}/retry-plan` | 读取本人失败/中止的独立 E1 原始重试方案；不创建任务 |
| `GET` | `/api/v1/runs/{run_id}` | 查看 run、slot 与候选摘要 |
| `GET` | `/api/v1/candidates/{candidate_id}` | 查看当前 owner 的候选代码、指标和审计结果 |
| `POST` | `/api/v1/runs/{run_id}/abort` | 请求取消 queued/running run |
| `GET` | `/api/v1/capabilities` | 查询 E2 是否开放及限制；UI/BFF 的唯一开关来源 |
| `POST` | `/api/v1/evolution-loops/prepare` | 无模型配置的 owner 预检：明确新窗口、种子、事件快照与必要覆盖；不授权执行 |
| `POST` | `/api/v1/evolution-loops/preflight` | 对完整启动请求复核必要输入，不创建运行 |
| `POST` | `/api/v1/evolution-loops/start` | 签名、预算与幂等绑定的闭环启动 |
| `POST` | `/api/v1/evolution-loops` | 从稳定 target reference 创建或幂等复用自动闭环 |
| `GET` | `/api/v1/evolution-loops` | 按 `limit` 列出当前 owner 最近的闭环 |
| `GET` | `/api/v1/evolution-loops/{loop_id}` | 查看阶段、下一动作与关联 E1/E2/Forward/holdout |
| `GET` | `/api/v1/evolution-loops/{loop_id}/events` | 按版本读取 SSE 增量 |
| `POST` | `/api/v1/campaigns` | 创建或幂等复用冻结的 E2 campaign |
| `GET` | `/api/v1/campaigns` | 按 `limit` 列出当前 owner 的 campaign |
| `GET` | `/api/v1/campaigns/{campaign_id}` | 查看 campaign 与代际摘要 |
| `POST` | `/api/v1/campaigns/{campaign_id}/start` | 启动仍处于 draft 的 campaign |
| `POST` | `/api/v1/campaigns/{campaign_id}/adopt` | 手动采用通过门禁的实验性赢家 |
| `GET` | `/api/v1/adoptions` | 列出当前 owner 的实验性采用记录 |

E1 准备契约（正常 owner 路径已有生产回执，恢复边界见当前状态）：先按正常用户 JWT 调用预检，将返回的
`seed_source_hash` 与 `dataset_manifest.content_sha256` 作为请求的
`preparation.seed_source_hash` / `preparation.dataset_content_sha256`。费用审批和 Ed25519
请求摘要均覆盖这两个字段。缺少 preparation 时，付费 `/runs` 返回
`EVOLUTION_PREPARATION_REQUIRED`；种子或执行前数据变化时返回/记录
`EVOLUTION_PREPARATION_CHANGED`，需重新预检并审批，不继续调用模型。
自建部署需同步升级 orchestration 与 Evolver；现有 E2 内部 baseline 请求摘要和历史运行
保持兼容，此准备接口不授予 E2、采用或交易权限。预检会通过正常 Data API 准备真实行情，
不是完全无副作用的查询，也不证明已有策略会盈利。

显式失败重试（本分支实现，线上验收待完成）：详情页“修复后重试”会请求原始重试方案，
保持原种子源码、评估窗口、策略参数、执行费用模型和候选数，再经过预检与新的费用审批。
`evolver.run_evolution` 的 `retryOfRunId` 绑定父尝试并进入签名摘要，数据库迁移 0060 为每次
尝试保存 `experiment_id`、`retry_of_run_id` 和 `attempt_number`。普通重复请求复用同一尝试，
同一实验最多一个活跃尝试；各次失败记录与已有费用保留。历史缺少冻结源码的任务不能重试。
E2 内部 baseline 走原工作流恢复，不支持通过这个入口重试，也不会重复消费 holdout。
新的模型快照仍由当前 owner 选择并单独审批；重试不会继承旧 credential grant。
迁移 0060 在已有重试血缘时拒绝降级，避免丢失审计关系。

编排层为 E1 暴露 `evolver.get_retry_plan`、`evolver.run_evolution`、`evolver.get_evolution`、
`evolver.get_candidate`、`evolver.abort_evolution`，并为 E2 暴露 `evolver.resolve_target`、
`evolver.run_event_campaign` 与 `evolver.get_event_campaign`。

## 配置与启动

主要环境变量统一放在仓库根 `.env`：

| 变量 | 用途 |
|---|---|
| `DATABASE_URL` | run/candidate 持久化 |
| `DATA_SERVICE_URL` | 获取并冻结真实 bars |
| `DASHBOARD_SERVICE_URL` | 即时读取 owner LLM 配置的内部服务地址 |
| `JWT_SECRET` / `JWT_ALGORITHM` | 用户与 service JWT 验证 |
| `EVOLVER_POOL_SIZE` | PostgreSQL 连接池大小 |
| `EVOLVER_MAX_RUNNING_RUNS` | 服务级同时运行上限 |
| `EVOLVER_ACCOUNT_ACTIVE_LIMIT` | 单 owner active run 上限 |
| `EVOLVER_QUEUE_TIMEOUT_S` | queued 最长等待（默认 24 小时，超时显式 abort；短于 grant 的 30 小时 TTL） |
| `EVOLVER_JOB_TIMEOUT_S` / `EVOLVER_RUN_TIMEOUT_S` | 单候选与整次 run 超时 |
| `EVOLVER_JOB_MEM_GB` | 回测子进程内存上限 |
| `EVOLVER_LLM_TIMEOUT_S` | 单次 LLM 变异超时 |
| `EVENT_EVOLUTION_ENABLED` | E2 capability 开关；默认 `false`，不影响 E1 |
| `CAMPAIGN_LEASE_TTL_S` | campaign worker lease 与 fencing 的有效期 |
| `CAMPAIGN_MAX_CONCURRENT` | 单个 Evolver 进程的 E2 campaign 并发上限 |
| `CANDIDATE_EVALUATION_CONCURRENCY` | 单 campaign 候选确定性评估并发，范围 1–4 |

owner 凭据 capability 使用 Ed25519。生成 DER/base64 密钥：

```bash
openssl genpkey -algorithm ED25519 -out /tmp/inalpha-evolution-private.pem
openssl pkey -in /tmp/inalpha-evolution-private.pem -outform DER | base64 | tr -d '\n'
openssl pkey -in /tmp/inalpha-evolution-private.pem -pubout -outform DER | base64 | tr -d '\n'
```

依次填入 `EVOLUTION_CREDENTIAL_PRIVATE_KEY_B64` 与
`EVOLUTION_CREDENTIAL_PUBLIC_KEY_B64`。生产私钥只注入 orchestration；Dashboard 只持有公钥，
Evolver 显式移除私钥，因此不能自行签发任意 owner/config 的明文凭据读取授权。

```bash
cd infra/migrations && uv run alembic upgrade head && cd ../..
cd services/evolver
uv sync
uv run uvicorn inalpha_evolver.main:app --port 8005 --reload

uv run ruff check .
uv run pytest
```

### 独立测试库验证持久化闭环

从 `services/evolver` 执行：

```bash
uv run python scripts/validate_durable_loop.py
uv run python scripts/validate_durable_loop.py --include-five-generations --evaluation-concurrency 4
```

脚本只接受本机 PostgreSQL 连接，使用 `DATABASE_URL`（环境变量优先，其次根 `.env`）
创建随机命名的空测试库，迁移后运行恢复、重复请求、租约 fencing、阶段交接和预算测试，
迁移最多运行 120 秒，测试最多运行 600 秒；正常退出、超时及 SIGTERM 会执行测试库清理。
SIGKILL、断电或数据库不可用可能留下 `inalpha_loop_check_<随机 ID>`，需按日志名称手动清理。数据库用户须有创建数据库权限；不会清空开发库或重启现有服务。
日志与 JUnit 结果保存在根 `.tmp/loop-validation/`。五代检查使用真实评估引擎与合成数据，
外部模型为离线替身，不产生 LLM 费用；它仍不能代替真实市场事件、付费模型或 Forward 验收。

验证脚本自动发现 `tests/test_loop_*.py`，五代评估仍需显式启用。连接 URL 中覆盖主机、
数据库或 service 的参数会被拒绝；libpq 与 Alembic 均显式固定本机地址和随机测试库，
支持 IPv6，并避免环境变量 `PGHOSTADDR` 改变连接目标。

仓库级开发推荐直接运行 `bash scripts/dev.sh`。要在本地试用 E2，在根 `.env` 设置
`EVENT_EVOLUTION_ENABLED=true`，并通过真实来源采集事件，同时启用 `EVENT_ARCHIVE_ENABLED=true`
与 `EVENT_EXTRACTION_ENABLED=true`。重启服务后，登录并在策略、
模拟盘或 E1 结果页点击“开始进化”，也可以直接在右侧 Agent 输入同一句话。重复触发会复用
同一活动 loop。新准备页不调用模型；聊天准备费用另计，不能把生成未启动写成整体零费用。无可见事件时返回 `EVENT_SNAPSHOT_EMPTY`。E2 仍限定研究环境；MAP-Elites / Island Model 在拿到真实成功率、拒绝分布和
费用样本后再评估。


### 显式新实验与 E2 Loop 费用审批

Dashboard 的准备页通过普通 owner 身份调用 orchestration `/evolution/prepare`，后者解析本人种子、冻结 Data 事件快照并调用 Evolver `/api/v1/evolution-loops/prepare`。预检可能通过正常 Data API 补齐行情和创建快照，不是完全无写入的查询；不会创建 Loop、兑换模型密钥或调用模型。

默认新窗口依据真实事件 `available_at` 和闭合行情，独立于种子旧回测窗口。显示自然 60/20/20 分段；独立事件上界满足最低必要输入不代表匹配对照或 FDR 已通过。不能回溯可见时间、改写旧实验或按收益选择窗口。

`/evolution/approval` 通过既有 hooks 创建冻结费用审批。E2 Loop 现在与 E1 一样，正常用户明确批准后才直接提交；独立 campaign API 保持既有授权语义。准备页不通过聊天申请审批，故不产生准备聊天费用。审批包含完整配置、事件快照、种子与行情摘要、冻结模型和硬预算，修改需要重新准备。执行仍经 owner/JWT、orchestration Ed25519 grant 与 Dashboard 模型兑换；不新增明文凭据持久化。

部署需先完成 `0067`（依赖 `0066`）迁移，再同步 Dashboard、orchestration、Evolver。已有 Loop 批准记录时降级迁移会拒绝，不自动删除命令。批准命令沿用原 AES-GCM 与最长 24 小时恢复窗口，`JWT_SECRET` 轮换旧密文限制不变。所有生产状态和剩余验收统一见 [当前状态](../../docs/04-current-state.md#未完成--下一步)。

### 隔离完整应用进程检查

```bash
# 仅接受本地 PostgreSQL；创建新审计库并保留，不读取或删除旧 audit 库。
DATABASE_URL="<local-postgresql-url>" .venv/bin/python scripts/validate_process_recovery.py
```

该检查启动未修改的完整 Evolver 应用并两次重启，验证合成独立 E1 中断记录的收口和费用保留。未创建有效 owner/grant，不调用模型；不能代替 E2 授权命令恢复、在途模型请求、Forward/holdout 或生产重启验收。

此脚本需要 POSIX 信号（Linux/macOS），拒绝 Python 优化模式；失败时保留异常类型与结构化回执，不写原始异常正文。

审计库按本任务授权保留，每次运行均创建一个新的完整 schema；脚本输出库名与回执路径。请控制手动运行次数，存储回收需另行明确授权，脚本不自动删除审计证据。

隔离 checkout 必须没有根、Evolver 工作目录或 infra 的 `.env` 文件；请通过环境变量传入本地 DATABASE_URL。配置的下游端点使用拒绝请求的本地接收器，计数不等于全部出站网络审计。SIGTERM 退出码单独记录，不据此宣称退出码 0 的优雅停机验收。

### Loop 提案响应恢复（0068）

Loop 每代两批模型请求分别持久化 dispatch、响应与费用预留，随后才提交整代假设。已保存响应在有效 owner/授权/租约下复用，不再次调用模型；第一代 scaffold 身份按 campaign 稳定生成。原始 prompt 摘要绑定完整请求，参数变化不能复用旧响应。

请求已登记但响应未保存时，系统无法证明供应商是否计费：保留预留费用并以 `LOOP_PROPOSAL_RESPONSE_UNCONFIRMED` 停止自动重发，也不会启动同代另一批新请求来绕过此状态。应先核对用量，再明确准备并批准新实验；不清除旧预留或伪装为免费重试。响应已保存但缺少 usage 时仍保留未知费用。

该功能先迁移 `0068`（依赖 `0067`），再部署 Evolver；存在批次记录时禁止降级删除。独立 non-Loop campaign 不使用此缓存。53 项相关回归及离线进程注入已通过，生产部署/正常 owner 续跑尚待，详细范围见 [回执](../../docs/validation/e2-proposal-response-recovery-2026-10-12.json)。

### 固定镜像接手与旧租约写入检查

```bash
# 先缓存对应业务 SHA 的 linux/amd64 镜像；本命令只使用已有缓存的固定 image ID。
DATABASE_URL="<local-postgresql-url>" .venv/bin/python scripts/validate_campaign_takeover.py \
  --image "ghcr.io/mirror29/inalpha-evolver:<40-character-business-sha>"
```

需要本地 PostgreSQL、Docker、Evolver dev 依赖和 migrations 虚拟环境。工具新建并迁移 UUID 审计库，启动两个完整 Evolver 应用；先以当前有效 token 对同一 reservation 执行并回滚 `proposal_responses.complete()` 作为正向对照；再暂停旧进程，等待真实租约过期，观察接手的新 token，并尝试旧 token 的相同写入。旧请求必须被拒绝，未知费用预留及阶段不得改变。外围 Data、签发与模型 HTTP 是合成夹具，不购买真实模型调用；不是正常 owner 或生产授权验收，也不证明旧 SDK 回调成功收包。

回执和私有日志写入仓库 `.tmp/e2-takeover-validation/`；审计库保留，只清理本次 UUID 容器及临时 env。工具明确记录清理错误，不把 Docker 查询失败当作容器已删除。临时隔离响应表锁用于观察领取状态，回执记录取得/释放时间；失败阶段写入回执，traceback 只保存在私有日志中。合成 baseline 故意复用 checkout 的 `tests.test_loop_handoff.ready_loop`（需要 dev 依赖），由路径定位和 CI 契约测试防止无声漂移，不是可安装的生产 seed 接口。单次检查仍不能替代完整故障矩阵、真实五代搜索和长期 Forward/holdout；剩余清单见 [当前状态](../../docs/04-current-state.md#未完成--下一步)。
