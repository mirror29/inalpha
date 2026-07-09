/**
 * 单点维护的发布元信息。
 *
 * 任何展示 last update 的位置（TickerStrip / CTAFooter / ConsoleSidebar）
 * 都从这里读，避免散落硬编码。
 *
 * - 日期通过 `getLastUpdate()` 函数获取，在 build 时由 GitHub API 动态拉取。
 * - 无 GITHUB_TOKEN 时走匿名 60 req/h/IP，fallback 有兜底。
 */

import { REPO_COORDS } from "./links";

/**
 * 获取 GitHub 仓库最近一次 commit 的日期。
 * 返回 broadsheet 点号写法（如 "2026.07.09"），失败返回 null。
 */
async function fetchLastCommitDate(): Promise<string | null> {
  const { owner, name } = REPO_COORDS;
  const url = `https://api.github.com/repos/${owner}/${name}/commits?per_page=1`;

  const headersInit: Record<string, string> = {
    Accept: "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
  };

  const token = process.env.GITHUB_TOKEN;
  if (token) headersInit.Authorization = `Bearer ${token}`;

  const headers = new Headers(headersInit);

  try {
    const res = await fetch(url, { headers });
    if (!res.ok) return null;

    const commits = (await res.json()) as Array<{
      commit: { committer?: { date?: string } };
    }>;

    if (!Array.isArray(commits) || commits.length === 0) return null;

    const dateStr = commits[0].commit?.committer?.date;
    if (!dateStr) return null;

    // ISO → broadsheet 点号（2026-07-09T12:34:56Z → 2026.07.09）
    return dateStr.split("T")[0].replace(/-/g, ".");
  } catch {
    return null;
  }
}

/** 兜底日期（API 失败时使用）。 */
export const FALLBACK_DATE = "2026.06.17";

/**
 * Build 时获取 last update 日期，带兜底。
 * 在 page / layout 的 server component 中调用。
 */
export async function getLastUpdate(): Promise<string> {
  const date = await fetchLastCommitDate();
  return date ?? FALLBACK_DATE;
}

/**
 * 生成 TickerStrip / CTAFooter 使用的短串。
 * 格式：`Updated 2026.07.09`
 */
export async function getUpdateTag(): Promise<string> {
  const date = await getLastUpdate();
  return `Updated ${date}`;
}

/**
 * 生成 ConsoleSidebar 使用的 build 标签。
 * 格式：`Build · 2026.07.09`
 */
export async function getBuildTag(): Promise<string> {
  const date = await getLastUpdate();
  return `Build · ${date}`;
}