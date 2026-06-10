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

async function countWithPagination(url: string): Promise<number | null> {
  const res = await fetch(url, { headers: headers() });
  if (!res.ok) return null;
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

    const [repoRes, contributors, commits] = await Promise.all([
      fetch(base, { headers: headers() }),
      countWithPagination(`${base}/contributors?per_page=1&anon=true`),
      countWithPagination(`${base}/commits?per_page=1`),
    ]);

    if (!repoRes.ok || contributors === null || commits === null) return null;
    const repoJson = (await repoRes.json()) as { stargazers_count?: number };
    if (typeof repoJson.stargazers_count !== "number") return null;

    return { stars: repoJson.stargazers_count, contributors, commits };
  } catch {
    return null;
  }
}
