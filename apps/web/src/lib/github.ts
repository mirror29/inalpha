/**
 * GitHub repo stats fetcher（server-only，build 时执行）。
 *
 * 用法（server component）：
 *   const stats = await getGithubStats({ owner: "mirror29", repo: "inalpha" });
 *
 * 站点 `output: "export"` 静态导出 —— 本 fetch 只在 `next build` 时跑一次，
 * 数字随 HTML 冻结到下次部署。「更新频率 = 部署频率」是有意为之：
 * 浏览器直连 GitHub API 的方案试过，未鉴权 60 req/h/IP 在共享出口 IP 下
 * 极易 403，体验反而更差。
 *
 * 设计：
 * - stars：`/repos/:o/:r` 一次拿；
 * - contributors / commits：`per_page=1` + 解析 `Link: ...; rel="last"` 拿总数，
 *   避免拉完整分页（仓库 commit 可能上千，全拉是浪费）；
 * - `GITHUB_TOKEN`（CI secret / 本地环境变量）有则带上，5000 req/h，
 *   build 稳定出数；没有则未鉴权裸跑；
 * - 任一指标拿不到即整体返 `null` —— 调用方直接不展示该组数字，
 *   没有编造的兜底假数据。
 *
 * 诊断：因为静默返 null 会把「限流 403 / token 失效 401 / 网络抖动」
 * 抹成同一种「数字消失」，失败分支统一用 `[github]` 前缀打到构建日志，
 * 并带上 HTTP 状态与 `x-ratelimit-remaining`，便于在 Cloudflare build log
 * 里一眼定位是哪一类失败（详见 `apps/web/README.md` 部署排障）。
 */

export interface GithubStats {
  stars: number;
  contributors: number;
  commits: number;
}

interface GetStatsOptions {
  owner: string;
  repo: string;
}

const HEADERS_BASE: HeadersInit = {
  Accept: "application/vnd.github+json",
  "X-GitHub-Api-Version": "2022-11-28",
};

function authHeaders(): HeadersInit {
  const token = process.env.GITHUB_TOKEN;
  return token ? { Authorization: `Bearer ${token}` } : {};
}

function headers(): HeadersInit {
  return { ...HEADERS_BASE, ...authHeaders() };
}

/**
 * 从 `Link: <…&page=N>; rel="last"` 头里解析 last page 号；
 * 当总数 <= per_page 时 GitHub 不返 Link，此时计 body 长度即可。
 */
function parseLastPage(linkHeader: string | null): number | null {
  if (!linkHeader) return null;
  const match = linkHeader.match(/<[^>]*[?&]page=(\d+)[^>]*>;\s*rel="last"/);
  return match ? Number(match[1]) : null;
}

/**
 * 把一次 GitHub 响应的失败原因打到构建日志：HTTP 状态 + 限流余额。
 * `x-ratelimit-remaining: 0` ⇒ 撞限流；`401` ⇒ token 失效/无效。
 *
 * @param {string} label - 调用名（stars / contributors / commits），用于定位
 * @param {Response} res - fetch 响应
 */
function logFailure(label: string, res: Response): void {
  const remaining = res.headers.get("x-ratelimit-remaining");
  console.warn(
    `[github] ${label} 失败：HTTP ${res.status} ${res.statusText}` +
      `（x-ratelimit-remaining=${remaining ?? "n/a"}）`,
  );
}

/**
 * @param {string} url - GitHub 分页计数端点
 * @param {string} label - 调用名，失败时写入日志
 */
async function countWithPagination(
  url: string,
  label: string,
): Promise<number | null> {
  const res = await fetch(url, { headers: headers() });
  if (!res.ok) {
    logFailure(label, res);
    return null;
  }
  const last = parseLastPage(res.headers.get("link"));
  if (last !== null) return last;
  const body = (await res.json()) as unknown[];
  return Array.isArray(body) ? body.length : null;
}

export async function getGithubStats({
  owner,
  repo,
}: GetStatsOptions): Promise<GithubStats | null> {
  try {
    const base = `https://api.github.com/repos/${owner}/${repo}`;
    const authed = Boolean(process.env.GITHUB_TOKEN);

    const [repoRes, contributors, commits] = await Promise.all([
      fetch(base, { headers: headers() }),
      countWithPagination(`${base}/contributors?per_page=1&anon=true`, "contributors"),
      countWithPagination(`${base}/commits?per_page=1`, "commits"),
    ]);

    if (!repoRes.ok) logFailure("stars", repoRes);
    if (!repoRes.ok || contributors === null || commits === null) {
      console.warn(
        `[github] ${owner}/${repo} 统计整体降级为 null` +
          `（GITHUB_TOKEN ${authed ? "已配置" : "缺失→未鉴权 60 req/h/IP，共享出口极易撞限流"}）`,
      );
      return null;
    }
    const repoJson = (await repoRes.json()) as { stargazers_count?: number };
    if (typeof repoJson.stargazers_count !== "number") {
      console.warn("[github] stars 响应缺 stargazers_count 字段，降级为 null");
      return null;
    }

    return { stars: repoJson.stargazers_count, contributors, commits };
  } catch (err) {
    console.warn(`[github] 拉取异常（网络/超时等），降级为 null：${String(err)}`);
    return null;
  }
}
