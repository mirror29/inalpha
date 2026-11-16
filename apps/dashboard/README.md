# @inalpha/dashboard · 认证操作者控制台

Inalpha 的动态 Next.js 控制台（`:3001`）：提供 agent 对话，以及组合、持仓、Live Runner、
回测、策略实验室、Evolver、因子、风控和活动追踪等操作者视图。它不是静态官网，也不再是
单用户只读 MVP。

## 与 `apps/web` 的关系

- `apps/web`：纯静态官网（`output: "export"` → Cloudflare Pages）。
- `apps/dashboard`：Node 运行时应用，负责登录/session、BFF、对话和操作界面。
- 两者独立构建与部署（`inalpha.dev` / `app.inalpha.dev`），共享品牌设计语言。

## BFF 与认证边界

浏览器只访问同源 `/api/*`。Dashboard Route Handlers 校验登录 session，再按用户签发后端
JWT 并访问 data/paper/research/factor/evolver/Mastra；Python services 不直接暴露给浏览器。

用户级 LLM provider/model/key 在服务端管理，key 以 `LLM_CONFIG_ENCRYPTION_KEY`（未配置时
兼容回退 `JWT_SECRET`）加密保存。Evolver 执行时只能转交由 orchestration 签发、绑定
owner / operation / `config_id` / digest 的短时 Ed25519 grant；Dashboard 用公钥验签并通过
PostgreSQL `jti` 兑换记录限制重放，再经 `/api/internal/llm-config/{id}` 按 owner 即时读取。
明文 key 不进入 run/candidate 记录，grant 成功兑换后从运行队列清除。

## 本地启动

```bash
# 仓库根：开发数据库 + migration + 五个 Python services + Mastra
cp infra/.env.example infra/.env
(cd infra && docker compose up -d)
(cd infra/migrations && uv sync && uv run alembic upgrade head)
bash scripts/dev.sh

# 控制台
cd apps/dashboard
pnpm install
pnpm dev                     # http://localhost:3001
```

打开 `http://localhost:3001/zh` 或 `/en`。启用认证的环境需先运行
`bash scripts/selfhost.sh create-user --email you@example.com` 创建用户。

## 环境变量

控制台通过 `next.config.ts` 默认读取仓库根 `.env`。常用配置：

| 变量 | 用途 |
|---|---|
| `AUTH_ENABLED` | 是否启用登录闸门 |
| `JWT_SECRET` / `JWT_ALGORITHM` | 用户/服务 JWT |
| `LLM_CONFIG_ENCRYPTION_KEY` | 用户 LLM API key 的独立加密密钥 |
| `EVOLUTION_CREDENTIAL_PUBLIC_KEY_B64` | 验证 Evolver owner 凭据 grant 的 Ed25519 公钥 |
| `DATA_SERVICE_URL` … `EVOLVER_SERVICE_URL` | 五个 Python service 地址 |
| `MASTRA_URL` | agent 编排地址 |
| `EVOLVER_ENABLED` | 是否显示并开放演化能力 |

只有局部覆盖时才复制 `.env.local.example` 为 `.env.local`；该文件优先级高于根 `.env`，且
不得提交任何 secret。

## 主要页面与 API

`src/app/[locale]/` 包含 overview、runners、activity、backtests、lab、evolution、factors、risk
等页面；`src/app/api/` 提供相应 BFF routes，并包含 `auth`、`chat`、`copilotkit`、`user/settings`
和 owner-scoped internal LLM config route。

```bash
cd apps/dashboard
pnpm typecheck
pnpm test
pnpm build
```

认证、演化与部署边界也见仓库根 README 和
[`docs/01-architecture-overview.md`](../../docs/01-architecture-overview.md)。

### 试用审批邮件

管理员批准试用或重新生成激活链接后，Dashboard 服务端使用 SMTP 自动发送中英双语激活邮件。
收件地址来自 Paper 审批结果，链接有效期仍为 48 小时；邮件服务器接受邮件不代表最终送达。
拒绝申请和审批冲突不会发信。SMTP 失败不回滚审批，页面保留复制链接的手动兜底；
重新生成会使旧链接失效，不进行后台自动重试。

配置 `SMTP_HOST`、`SMTP_PORT`（默认 465，隐式 TLS）、`SMTP_USER`、`SMTP_PASSWORD`
及 `ACTIVATION_PUBLIC_URL`（公开 HTTPS 控制台地址）。发件人使用 `SMTP_USER`。
163 邮箱须开启 SMTP，并使用客户端授权码而非登录密码；证书校验保持开启。
本地可写入忽略的根目录 `.env.local`；生产写入 `infra/.env.prod`，更新 Paper 与 Dashboard
镜像并重建 Dashboard 容器使配置生效。密钥不得提交版本库或写入日志。
