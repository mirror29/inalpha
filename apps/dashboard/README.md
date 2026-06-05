# @inalpha/dashboard · 操作者控制台

Inalpha 的**只读运行时看板**——把"原本要问 agent 才知道的状态"(账户/持仓/Live Runner/Agent 活动/回测史)变成一眼可见的盘面。

> 设计文档:`docs/miro/14-observability-dashboard-design.md`
> 定位:开发/操作者控制台(单用户 · dev token),**不是**多租户产品。当前已落地:**组合总览 MVP**。

## 与官网(apps/web)的关系

- `apps/web` = 纯静态官网(`output: "export"` → Cloudflare Pages)。
- `apps/dashboard` = **动态** Next 应用(Node 运行时),用 Route Handler 当 BFF。
- 两者独立工程、独立依赖、独立部署(`inalpha.dev` / `app.inalpha.dev`)。视觉语言共用同一套 token。

## 为什么要 BFF

python service(8001/8002/8003)**没配 CORS**,浏览器不能直连;且后端要 JWT。
所以浏览器只调同源 `/api/*` → server 侧 BFF 用 dev token 转发到后端。token 不进浏览器。

## 本地起

```bash
# 1. 先把后端 + mastra 拉起(在仓库根)
bash scripts/dev.sh up        # data:8001 paper:8002 research:8003 mastra:4111

# 2. 装依赖 + 起控制台
cd apps/dashboard
pnpm install
pnpm dev                       # http://localhost:3001
```

打开 `http://localhost:3001/zh`(或 `/en`)。

### 环境变量

控制台**默认直接读仓库根的 `.env`**(后端 service URL + `JWT_SECRET` 都在那),
不用单独维护一份(逻辑在 `next.config.ts` 的 `loadRootEnv`)。

只在需要**局部覆盖**时,才在 `apps/dashboard/` 建 `.env.local`(见
`.env.local.example`)——比如切换控制台身份 `CONSOLE_SUBJECT`、或指向远端后端。
`.env.local` 优先级高于根 `.env`。

## 结构

```
src/
├── app/
│   ├── [locale]/            # 看板页面(next-intl: en/zh)
│   │   ├── layout.tsx       # 控制台外壳(导航 + 状态条)
│   │   └── page.tsx         # ① 组合总览
│   └── api/                 # BFF —— server 侧聚合后端接口
│       └── overview/route.ts
├── components/
│   ├── primitives/          # 移植自官网的设计原子
│   ├── shell/               # 导航 / 状态条
│   └── overview/            # 组合总览的 KPI/持仓/订单
└── lib/
    ├── backend.ts           # getServiceToken() + 带鉴权 fetch + 后端 base url
    ├── types.ts             # 后端 schema 的 TS 镜像
    └── format.ts            # 数字/货币/时间格式化
```

## 路线图(后续看板)

② Live Runner 监控 → ③ Agent 运行日志/可观测性 → ④ 策略实验室+回测史 → 风控面板。
