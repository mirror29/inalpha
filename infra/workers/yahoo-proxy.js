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

const HOST_MAP = {
  query1: "query1.finance.yahoo.com",
  query2: "query2.finance.yahoo.com",
  finance: "finance.yahoo.com",
  fc: "fc.yahoo.com",
};

export default {
  async fetch(request) {
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

    // 转发原始请求头（含 Cookie —— 别丢，它是 yfinance 认证契约的一部分），只删 CF 注入头
    // 避免干扰 Yahoo。data service 走 curl_cffi（impersonate=chrome），UA 本就是浏览器 UA，
    // 无需改写；仅当 UA 缺失 / 是 CF 默认值时兜底成浏览器 UA（防 python-requests UA 被 edge 拦）。
    //
    // ⚠️ crumb 认证端点的已知限制：.info / v10 quoteSummary 靠 fc/guce/consent/query 跨多个
    // Yahoo 子域的 cookie+crumb 握手，URL 改写把出口主机换成 workers.dev 后这套握手无法还原
    // （实测即便转发 Cookie + 剥离 Set-Cookie 的 Domain 仍返 401 Invalid Crumb）。故 fetch_financials
    // 走代理时会降级 available=false（直连才可用，但线上 IP 被封直连也不通）。chart/history 实时
    // 报价路径不吃 crumb，代理完全可用——那才是本代理要解决的目标。
    const headers = new Headers(request.headers);
    headers.set("Host", targetHost);
    for (const key of ["CF-Connecting-IP", "CF-IPCountry", "CF-RAY", "CF-Visitor", "CDN-Loop"]) {
      headers.delete(key);
    }
    const ua = headers.get("User-Agent") || "";
    if (!ua || ua.includes("cloudflare") || ua.length < 10) {
      headers.set("User-Agent", "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36");
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
