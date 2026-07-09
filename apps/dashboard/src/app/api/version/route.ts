/**
 * Dashboard API route：返回 GitHub last update 日期。
 *
 * 调用 GitHub API 获取最近 commit 时间，返回 broadsheet 格式（如 "2026.07.09"）。
 * - 有 GITHUB_TOKEN 则带上（5000 req/h），否则匿名（60 req/h/IP）。
 * - 失败时返回 fallback 日期。
 */

import { NextResponse } from "next/server";

const REPO = {
  owner: "mirror29",
  name: "inalpha",
} as const;

const FALLBACK_DATE = "2026.06.17";

export async function GET() {
  const url = `https://api.github.com/repos/${REPO.owner}/${REPO.name}/commits?per_page=1`;

  const headers: HeadersInit = {
    Accept: "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
  };

  const token = process.env.GITHUB_TOKEN;
  if (token) {
    headers.Authorization = `Bearer ${token}`;
  }

  try {
    const res = await fetch(url, { headers });
    if (!res.ok) {
      return NextResponse.json({ date: FALLBACK_DATE });
    }

    const commits = (await res.json()) as Array<{
      commit: { committer?: { date?: string } };
    }>;

    if (!Array.isArray(commits) || commits.length === 0) {
      return NextResponse.json({ date: FALLBACK_DATE });
    }

    const dateStr = commits[0].commit?.committer?.date;
    if (!dateStr) {
      return NextResponse.json({ date: FALLBACK_DATE });
    }

    // ISO → broadsheet 点号（2026-07-09T12:34:56Z → 2026.07.09）
    const isoDate = dateStr.split("T")[0];
    const date = isoDate.replace(/-/g, ".");

    return NextResponse.json({ date });
  } catch {
    return NextResponse.json({ date: FALLBACK_DATE });
  }
}
