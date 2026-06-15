/**
 * approval-identity · projectApprovalInput 单测。
 *
 * 重点覆盖**身份字段缺失退化**:projectApprovalInput 绝不能在投影为空时返回 {},
 * 否则 stableStringify({})="{}" 成同 session 万能 cache key,一次批准放行后续(CR #85)。
 */
import { describe, expect, it } from "vitest";

import { projectApprovalInput } from "../src/permissions/approval-identity.js";

describe("projectApprovalInput", () => {
  it("已登记 tool:投影到身份字段(promote → candidateId,丢弃 reason)", () => {
    const out = projectApprovalInput("paper.promote_candidate", {
      candidateId: "c-42",
      reason: "随便什么审计文本",
    });
    expect(out).toEqual({ candidateId: "c-42" });
  });

  it("不同候选投影不同(各自审批,不串号)", () => {
    const a = projectApprovalInput("paper.promote_candidate", { candidateId: "c-1", reason: "x" });
    const b = projectApprovalInput("paper.promote_candidate", { candidateId: "c-2", reason: "y" });
    expect(a).not.toEqual(b);
  });

  it("未登记 tool:原样返回完整 input", () => {
    const input = { foo: 1, bar: "z" };
    expect(projectApprovalInput("some.other_tool", input)).toBe(input);
  });

  it("身份字段缺失:退回完整 input,绝不返回 {}(防万能 key)", () => {
    const input = { reason: "缺了 candidateId" };
    const out = projectApprovalInput("paper.promote_candidate", input);
    // 关键:不能是 {};必须是完整 input,这样两个不同的缺身份调用投影后仍不同。
    expect(out).not.toEqual({});
    expect(out).toBe(input);
  });

  it("两个缺身份且内容不同的调用,投影后仍互不相等(不共享万能 key)", () => {
    const a = projectApprovalInput("paper.promote_candidate", { reason: "aaa" });
    const b = projectApprovalInput("paper.promote_candidate", { reason: "bbb" });
    expect(a).not.toEqual(b);
  });

  it("非 object input 原样返回", () => {
    expect(projectApprovalInput("paper.promote_candidate", null)).toBe(null);
    expect(projectApprovalInput("paper.promote_candidate", "x")).toBe("x");
  });
});
