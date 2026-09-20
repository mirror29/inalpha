import { describe, expect, it } from "vitest";

import {
  buildPageContextEnvelope,
  evolutionTargetFromPage,
  parsePageContext,
  stripPageContext,
} from "./page-context-shared";

const RUN_ID = "11111111-1111-4111-8111-111111111111";
const CANDIDATE_ID = "22222222-2222-4222-8222-222222222222";
const CAMPAIGN_ID = "33333333-3333-4333-8333-333333333333";

describe("evolution page context", () => {
  it("keeps the durable loop identity in chat before E2 exists", () => {
    const context = parsePageContext(`/evolution/loops/${RUN_ID}`);
    expect(context.kind).toBe("evolution_loop_detail");
    expect(buildPageContextEnvelope(context)).toContain("evolution_target_kind=evolution_loop");
    expect(buildPageContextEnvelope(context)).toContain(`evolution_target_id=${RUN_ID}`);
  });
  it("解析列表、运行详情与候选详情", () => {
    expect(parsePageContext("/evolution")).toEqual({
      kind: "evolution_list",
      pathname: "/evolution",
    });
    expect(parsePageContext(`/evolution/${RUN_ID}`)).toEqual({
      kind: "evolution_run_detail",
      id: RUN_ID,
      pathname: `/evolution/${RUN_ID}`,
    });
    expect(parsePageContext(`/evolution/candidates/${CANDIDATE_ID}`)).toEqual({
      kind: "evolution_candidate_detail",
      id: CANDIDATE_ID,
      pathname: `/evolution/candidates/${CANDIDATE_ID}`,
    });
  });

  it("详情 envelope 使用演化专用 id 键", () => {
    expect(
      buildPageContextEnvelope({
        kind: "evolution_run_detail",
        id: RUN_ID,
        pathname: `/evolution/${RUN_ID}`,
      }),
    ).toContain(`evolution_run_id=${RUN_ID}`);
  });

  it("解析回测和 E2 campaign 详情为统一演化目标", () => {
    expect(parsePageContext(`/backtests/${RUN_ID}`)).toEqual({
      kind: "backtest_run_detail",
      id: RUN_ID,
      pathname: `/backtests/${RUN_ID}`,
    });
    expect(parsePageContext(`/evolution/campaigns/${CAMPAIGN_ID}`)).toEqual({
      kind: "evolution_campaign_detail",
      id: CAMPAIGN_ID,
      pathname: `/evolution/campaigns/${CAMPAIGN_ID}`,
    });

    const envelope = buildPageContextEnvelope({
      kind: "evolution_campaign_detail",
      id: CAMPAIGN_ID,
      pathname: `/evolution/campaigns/${CAMPAIGN_ID}`,
    });
    expect(envelope).toContain("evolution_target_kind=e2_campaign");
    expect(envelope).toContain(`evolution_target_id=${CAMPAIGN_ID}`);
  });

  it.each([
    ["runner_detail", "paper_runner"],
    ["candidate_detail", "strategy_candidate"],
    ["backtest_run_detail", "backtest_run"],
    ["evolution_run_detail", "e1_run"],
    ["evolution_candidate_detail", "e1_candidate"],
    ["evolution_campaign_detail", "e2_campaign"],
  ] as const)("maps %s to %s", (kind, targetKind) => {
    expect(evolutionTargetFromPage({ kind, id: RUN_ID, pathname: "/detail" })).toEqual({
      kind: targetKind,
      id: RUN_ID,
    });
  });

  it("清除完整和被截断的上下文块", () => {
    expect(stripPageContext("<page_context>\npage=evolution_list\n</page_context>\n\nhello"))
      .toBe("hello");
    expect(stripPageContext("<page_context>\npage=evolution_run_detail")).toBe("");
  });
});
