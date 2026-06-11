# @inalpha/web

`inalpha.dev` 官网首页。Next.js 16 + Tailwind 4 + next-intl 4 + motion，静态化部署到 Cloudflare Pages。

独立 pnpm 包，不参与 root workspace（和 `packages/orchestration` 一样的约定）。

## 本地起服务

```bash
cd apps/web
pnpm i
pnpm dev      # http://localhost:3000
```

## 构建 / 类型检查

```bash
pnpm typecheck
pnpm build
```

## 结构

- `src/app/[locale]/` —— `en` 默认无前缀，`zh` 走 `/zh`
- `src/components/sections/` —— 7 个 section（Hero / TheLoop / KernelCards / Principles / MarketCoverage / EngineeringDiscipline / CTAFooter）
- `src/components/primitives/` —— DotGrid / CopyableCommand / LocaleSwitcher
- `messages/{en,zh}.json` —— 全部文案；新增 key 时两边同步
- `public/` —— mascot / favicon / og 图

## 设计 token

dark quant 终端调，色板见 `src/app/globals.css` 的 `@theme`：
- `bg #0a0e1a` / `bg-elev #11162a` / `fg #f5f5f7` / `fg-muted #9ba3b4` / `cyan #5fb3ff` / `border-subtle #1f2740`

## 部署

Cloudflare Pages → GitHub repo `mirror29/inalpha` → root directory `apps/web` → build `pnpm build` → output `.next`。绑域名 `inalpha.dev`。

### 环境变量

- `GITHUB_TOKEN`（**生产必配**）：CTA 区的 stars / contributors / commits 是 `next build` 时拉 GitHub API 的快照（`src/lib/github.ts`）。**不配** = 未鉴权 60 req/h/IP，Cloudflare 构建机走共享出口 IP 极易撞限流 → 三个数字整组静默隐藏（只剩硬编码 markets）。配上后按 token 独立计 5000 req/h，稳定出数。
  - 在 Cloudflare Pages → 项目 → Settings → Environment variables → **Production** 添加。
  - `mirror29/inalpha` 是公开库，只需读公开数据：classic PAT 勾 `public_repo`，或 fine-grained 给该仓库 public 只读即可。

### 排障：CTA 数字消失

失败分支会以 `[github]` 前缀打到构建日志，含 HTTP 状态与 `x-ratelimit-remaining`：
- `x-ratelimit-remaining=0` / `HTTP 403` → 撞限流，配 / 检查 `GITHUB_TOKEN`。
- `HTTP 401` → token 失效或无效，重置。
- `拉取异常` → 构建时网络/超时，重试部署。
