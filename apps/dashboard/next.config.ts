import { existsSync, readFileSync } from "node:fs";
import { resolve } from "node:path";
import type { NextConfig } from "next";
import createNextIntlPlugin from "next-intl/plugin";

const withNextIntl = createNextIntlPlugin("./src/i18n/request.ts");

/**
 * 直接复用仓库根的 .env(后端 service URL + JWT_SECRET 等都在那),省得 dashboard
 * 再维护一份。在 next.config 求值时把根 .env / .env.local 灌进 process.env——
 * **只填尚未设置的 key**,所以真实环境变量 + dashboard 自己的 .env.local(Next 会
 * 先加载)仍然优先,可做局部覆盖。
 */
function loadRootEnv(): void {
  const root = resolve(process.cwd(), "../..");
  for (const file of [".env", ".env.local"]) {
    const path = resolve(root, file);
    if (!existsSync(path)) continue;
    for (const raw of readFileSync(path, "utf8").split("\n")) {
      const line = raw.trim();
      if (!line || line.startsWith("#")) continue;
      const eq = line.indexOf("=");
      if (eq === -1) continue;
      const key = line.slice(0, eq).trim();
      if (!key || key in process.env) continue; // 已设置的不覆盖
      let val = line.slice(eq + 1).trim();
      if (
        (val.startsWith('"') && val.endsWith('"')) ||
        (val.startsWith("'") && val.endsWith("'"))
      ) {
        val = val.slice(1, -1);
      }
      process.env[key] = val;
    }
  }
}

loadRootEnv();

/**
 * 操作者控制台是**动态** Next 应用(对比官网 apps/web 的 `output: "export"`)。
 * 需要 Route Handler 当 BFF:server 侧持 dev JWT 转发到 python service,浏览器只调同源
 * `/api/*`,从而绕开 python service 未配 CORS + 不把 token 暴露到浏览器。
 * 因此这里**不能**设 `output: "export"`。
 */
const nextConfig: NextConfig = {
  reactStrictMode: true,
};

export default withNextIntl(nextConfig);
