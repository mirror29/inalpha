import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

// Regression: ISSUE-001 — 演化页面缺少 chat.context.kind 文案
// Found by /qa on 2026-08-14
// Report: .gstack/qa-reports/qa-report-localhost-3001-2026-08-14.md
const EVOLUTION_KINDS = [
  "backtest_run_detail",
  "evolution_list",
  "evolution_run_detail",
  "evolution_candidate_detail",
  "evolution_campaign_detail",
  "evolution_loop_detail",
] as const;

function messages(locale: "zh" | "en") {
  const path = resolve(process.cwd(), `messages/${locale}.json`);
  return JSON.parse(readFileSync(path, "utf8")) as {
    chat: {
      context: {
        evolve: string;
        evolvePrompt: string;
        kind: Record<string, string>;
      };
    };
  };
}

describe("evolution page-context messages", () => {
  it.each(["zh", "en"] as const)("%s 覆盖全部演化页面类型", (locale) => {
    const kinds = messages(locale).chat.context.kind;
    for (const kind of EVOLUTION_KINDS) {
      expect(kinds[kind]).toBeTruthy();
    }
    expect(messages(locale).chat.context.evolve).toBeTruthy();
    expect(messages(locale).chat.context.evolvePrompt).toBeTruthy();
  });
});
