/**
 * 外链 / 仓库坐标的单点常量。
 *
 * `LINKS.github` 是仓库主页；`LINKS.license` 是 LICENSE 文件直链。
 * 改 owner / repo 时只动 `REPO` 这里。
 */

const REPO = {
  owner: "mirror29",
  name: "inalpha",
} as const;

export const LINKS = {
  console: "https://dashboard.inalpha.dev",
  trial: (locale: string) => `https://dashboard.inalpha.dev/register?from=/${locale === "zh" ? "zh" : "en"}`,
  github: `https://github.com/${REPO.owner}/${REPO.name}`,
  license: `https://github.com/${REPO.owner}/${REPO.name}/blob/main/LICENSE`,
  /** 同一来源的 git clone 命令，供 Hero / CTAFooter 复用。 */
  gitClone: `git clone https://github.com/${REPO.owner}/${REPO.name}`,
} as const;

export const REPO_COORDS = REPO;
