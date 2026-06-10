/**
 * 单点维护的发布元信息。
 *
 * 任何展示 rev / phase / date 的位置（Hero / TickerStrip /
 * CTAFooter colophon）都从这里读，避免散落硬编码导致版本漂移。
 *
 * - 切换 milestone 时只动这里一处。
 * - `dateDot` 是 broadsheet 点号写法（2026.05.28），`dateIso` 给 SEO / 结构化数据。
 */

export const RELEASE = {
  rev: "0.11",
  phase: "D-11",
  /** Broadsheet 点号写法，供 UI 展示。 */
  dateDot: "2026.06.05",
  /** ISO，供 <time dateTime> 或后续 schema.org。 */
  dateIso: "2026-06-05",
} as const;

/** 复合短串，如 "rev 0.11 · D-11"。 */
export const releaseTag = `rev ${RELEASE.rev} · ${RELEASE.phase}`;

/** 复合长串，如 "0.11-D11 · 2026.06.05"。 */
export const releaseFootline = `${RELEASE.rev}-${RELEASE.phase.replace("-", "")} · ${RELEASE.dateDot}`;
