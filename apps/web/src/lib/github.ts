/**
 * GitHub repo stats fetcher（server-only）。
 *
 * 用法：
 *   const stats = await getGithubStats({ owner: "mirror29", repo: "inalpha" });
 *
 * 设计：
 * - stars：`/repos/:o/:r` 一次拿；
 * - contributors / commits：用 `per_page=1` + 解析 `Link: ...; rel=\"last\"` 拿总数，
 *   避免拉完整分页（仓库 commit 可能上千，全拉是浪费）；
 * - 缓存 1h（`next.revalidate`），失败兜底返 `null`，调用方各自决定 fallback 文案 / 数值。
 * - 未鉴权状态下 GitHub 给 IP 60 req/h，1h cache 足以承接 build + 偶发刷新；
 *   生产可注入 `GITHUB_TOKEN` 走 5000 req/h。
 */

export interface GithubStats {
  stars: number;
  contributors: number;
  commits: number;
}

interface GetStatsOptions {
  owner: string;
  repo: string;
  /** ISR 秒数，默认 3600。 */
  revalidate?: number;
}

const HEADERS_BASE: HeadersInit = {
  Accept: "application/vnd.github+json",
  "X-GitHub-Api-Version": "2022-11-28",
};

function authHeaders(): HeadersInit {
  const token = process.env.GITHUB_TOKEN;
  return token ? { Authorization: `Bearer ${token}` } : {};
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

async function countWithPagination(
  url: string,
  revalidate: number,
): Promise<number | null> {
  const res = await fetch(url, {
    headers: { ...HEADERS_BASE, ...authHeaders() },
    next: { revalidate },
  });
  if (!res.ok) return null;
  const last = parseLastPage(res.headers.get("link"));
  if (last !== null) return last;
  const body = (await res.json()) as unknown[];
  return Array.isArray(body) ? body.length : null;
}

export async function getGithubStats({
  owner,
  repo,
  revalidate = 3600,
}: GetStatsOptions): Promise<GithubStats | null> {
  try {
    const base = `https://api.github.com/repos/${owner}/${repo}`;

    const [repoRes, contributors, commits] = await Promise.all([
      fetch(base, {
        headers: { ...HEADERS_BASE, ...authHeaders() },
        next: { revalidate },
      }),
      countWithPagination(
        `${base}/contributors?per_page=1&anon=true`,
        revalidate,
      ),
      countWithPagination(`${base}/commits?per_page=1`, revalidate),
    ]);

    if (!repoRes.ok) return null;
    const repoJson = (await repoRes.json()) as { stargazers_count?: number };

    return {
      stars: repoJson.stargazers_count ?? 0,
      contributors: contributors ?? 0,
      commits: commits ?? 0,
    };
  } catch {
    return null;
  }
}
