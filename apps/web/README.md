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
