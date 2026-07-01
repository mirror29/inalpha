/**
 * Cloudflare Worker: Yahoo Finance 反向代理
 *
 * 部署: wrangler deploy 或 CF Dashboard → Workers → Create → 贴入此文件
 * 路由: yahoo.inalpha.dev (需先加 DNS CNAME → CF Workers 或直接用 Worker Routes)
 *
 * 原理: data service (新加坡 VPS, IP 被 Yahoo 反爬封) → yahoo.inalpha.dev (CF 边缘节点, 美国 IP)
 *       → query1.finance.yahoo.com / query2.finance.yahoo.com / finance.yahoo.com
 *
 * URL 映射:
 *   https://yahoo.inalpha.dev/query1/v8/finance/chart/AAPL   → query1.finance.yahoo.com
 *   https://yahoo.inalpha.dev/query2/v7/finance/options/AAPL  → query2.finance.yahoo.com
 *   https://yahoo.inalpha.dev/finance/quote/AAPL              → finance.yahoo.com
 *   https://yahoo.inalpha.dev/fc/...                          → fc.yahoo.com
 */

const HOST_MAP: Record<string, string> = {
  query1: "query1.finance.yahoo.com",
  query2: "query2.finance.yahoo.com",
  finance: "finance.yahoo.com",
  fc: "fc.yahoo.com",
};

export default {
  async fetch(request: Request): Promise<Response> {
    const url = new URL(request.url);

    // OPTIONS preflight (yfinance 不用, 但浏览器调试时方便)
    if (request.method === "OPTIONS") {
      return new Response(null, {
        status: 204,
        headers: {
          "Access-Control-Allow-Origin": "*",
          "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
          "Access-Control-Allow-Headers": "*",
          "Access-Control-Max-Age": "86400",
        },
      });
    }

    const segments = url.pathname.split("/").filter(Boolean);
    const hostKey = segments[0];
    const targetHost = HOST_MAP[hostKey];

    if (!targetHost) {
      return new Response(
        `unknown yahoo host key: "${hostKey}". valid: ${Object.keys(HOST_MAP).join(", ")}`,
        { status: 400 },
      );
    }

    // 拼接目标 URL
    const targetPath = "/" + segments.slice(1).join("/");
    const targetUrl = `https://${targetHost}${targetPath}${url.search}`;

    // 转发请求头 (去掉 CF 注入的 header 避免干扰 Yahoo)
    const headers = new Headers(request.headers);
    headers.set("Host", targetHost);
    headers.set("Accept-Encoding", "gzip, deflate");
    for (const key of ["CF-Connecting-IP", "CF-IPCountry", "CF-RAY", "CF-Visitor", "CDN-Loop"]) {
      headers.delete(key);
    }

    // 如果 UA 是 Worker 默认的 "cloudflare" 之类, 换成 curl (Yahoo 不拦 curl)
    const ua = headers.get("User-Agent") || "";
    if (ua.includes("cloudflare") || ua.length < 10) {
      headers.set(
        "User-Agent",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 yfinance-proxy/1.0",
      );
    }

    const response = await fetch(targetUrl, {
      method: request.method,
      headers,
      body: request.method !== "GET" && request.method !== "HEAD" ? request.body : undefined,
      redirect: "follow",
    });

    return response;
  },
};
