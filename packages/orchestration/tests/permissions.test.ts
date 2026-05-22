/**
 * Permissions 层单测：predicate parser / rule parser / engine 决策 / 默认规则。
 *
 * 这一层无运行时依赖，全部纯函数，易于穷举边界。
 */
import { describe, expect, it } from "vitest";

import {
  DEFAULT_PERMISSIONS,
  PermissionEngine,
  evaluatePredicate,
  mergeConfigs,
  parsePredicate,
  parseRule,
  patternMatches,
} from "../src/permissions/index.js";

// ────────────────────────────────────────────────────────────────────
// patternMatches
// ────────────────────────────────────────────────────────────────────

describe("patternMatches", () => {
  it("exact match", () => {
    expect(patternMatches("paper.run_backtest", "paper.run_backtest")).toBe(true);
    expect(patternMatches("paper.run_backtest", "paper.list_strategies")).toBe(false);
  });

  it("prefix .* match", () => {
    expect(patternMatches("paper.*", "paper.run_backtest")).toBe(true);
    expect(patternMatches("paper.*", "data.get_bars")).toBe(false);
  });

  it("global *", () => {
    expect(patternMatches("*", "anything")).toBe(true);
  });
});

// ────────────────────────────────────────────────────────────────────
// parsePredicate
// ────────────────────────────────────────────────────────────────────

describe("parsePredicate", () => {
  it("returns null for empty input", () => {
    expect(parsePredicate("")).toBeNull();
    expect(parsePredicate("   ")).toBeNull();
  });

  it("single < condition", () => {
    const p = parsePredicate("notional<1000");
    expect(p).not.toBeNull();
    expect(p!.conditions).toEqual([{ field: "notional", op: "<", value: 1000 }]);
  });

  it("supports all comparison ops", () => {
    expect(parsePredicate("x<=1")!.conditions[0]).toEqual({ field: "x", op: "<=", value: 1 });
    expect(parsePredicate("x>=1")!.conditions[0]).toEqual({ field: "x", op: ">=", value: 1 });
    expect(parsePredicate("x==1")!.conditions[0]).toEqual({ field: "x", op: "==", value: 1 });
    expect(parsePredicate("x!=1")!.conditions[0]).toEqual({ field: "x", op: "!=", value: 1 });
  });

  it("in [...] with bare identifiers", () => {
    const p = parsePredicate("symbol in [BTC, ETH, SOL]");
    expect(p!.conditions[0]).toEqual({
      field: "symbol",
      op: "in",
      value: ["BTC", "ETH", "SOL"],
    });
  });

  it("not in [...]", () => {
    const p = parsePredicate("status not in ['executed','rejected']");
    expect(p!.conditions[0]).toEqual({
      field: "status",
      op: "not in",
      value: ["executed", "rejected"],
    });
  });

  it("AND of multiple conditions", () => {
    const p = parsePredicate("size<0.05 && symbol in [BTC,ETH]");
    expect(p!.conditions).toHaveLength(2);
    expect(p!.conditions[0]!.field).toBe("size");
    expect(p!.conditions[1]!.field).toBe("symbol");
  });

  it("throws on malformed input", () => {
    expect(() => parsePredicate("notional<")).toThrow();
    expect(() => parsePredicate("notional 1000")).toThrow();
    expect(() => parsePredicate("notional<<1000")).toThrow();
  });
});

// ────────────────────────────────────────────────────────────────────
// evaluatePredicate
// ────────────────────────────────────────────────────────────────────

describe("evaluatePredicate", () => {
  it("numeric <", () => {
    const p = parsePredicate("notional<1000")!;
    expect(evaluatePredicate(p, { notional: 500 })).toBe(true);
    expect(evaluatePredicate(p, { notional: 1000 })).toBe(false);
    expect(evaluatePredicate(p, { notional: 1500 })).toBe(false);
  });

  it("missing field → numeric op evaluates to false", () => {
    const p = parsePredicate("size>0")!;
    expect(evaluatePredicate(p, {})).toBe(false);
  });

  it("AND short-circuits to false on first miss", () => {
    const p = parsePredicate("size<0.05 && symbol in [BTC]")!;
    expect(evaluatePredicate(p, { size: 0.1, symbol: "BTC" })).toBe(false);
    expect(evaluatePredicate(p, { size: 0.01, symbol: "ETH" })).toBe(false);
    expect(evaluatePredicate(p, { size: 0.01, symbol: "BTC" })).toBe(true);
  });

  it("supports nested path (orderParams.quantity)", () => {
    const p = parsePredicate("orderParams.quantity<1")!;
    expect(evaluatePredicate(p, { orderParams: { quantity: 0.5 } })).toBe(true);
    expect(evaluatePredicate(p, { orderParams: { quantity: 2 } })).toBe(false);
  });

  it("== with string", () => {
    const p = parsePredicate('venue=="binance"')!;
    expect(evaluatePredicate(p, { venue: "binance" })).toBe(true);
    expect(evaluatePredicate(p, { venue: "okx" })).toBe(false);
  });

  it("in [...] with string values", () => {
    const p = parsePredicate("symbol in [BTC, ETH]")!;
    expect(evaluatePredicate(p, { symbol: "BTC" })).toBe(true);
    expect(evaluatePredicate(p, { symbol: "BNB" })).toBe(false);
  });
});

// ────────────────────────────────────────────────────────────────────
// parseRule
// ────────────────────────────────────────────────────────────────────

describe("parseRule", () => {
  it("plain tool pattern", () => {
    const r = parseRule("paper.*", "allow");
    expect(r.toolPattern).toBe("paper.*");
    expect(r.predicate).toBeNull();
    expect(r.decision).toBe("allow");
  });

  it("tool pattern + predicate", () => {
    const r = parseRule("live.submit_order(notional>1000)", "deny");
    expect(r.toolPattern).toBe("live.submit_order");
    expect(r.predicate).not.toBeNull();
    expect(r.predicate!.conditions[0]).toEqual({ field: "notional", op: ">", value: 1000 });
  });

  it("throws on missing closing paren", () => {
    expect(() => parseRule("tool.x(a<1", "allow")).toThrow();
  });
});

// ────────────────────────────────────────────────────────────────────
// PermissionEngine
// ────────────────────────────────────────────────────────────────────

describe("PermissionEngine", () => {
  it("deny rule wins over allow", () => {
    const e = new PermissionEngine({
      defaultMode: "ask",
      allow: ["paper.*"],
      ask: [],
      deny: ["paper.submit_order"],
    });

    const r = e.authorize("paper.submit_order", {});
    expect(r.decision).toBe("deny");
    expect(r.matchedRule).toBe("paper.submit_order");
  });

  it("falls through to defaultMode when no match", () => {
    const e = new PermissionEngine({
      defaultMode: "ask",
      allow: ["paper.*"],
      ask: [],
      deny: [],
    });

    const r = e.authorize("unknown.tool", {});
    expect(r.decision).toBe("ask");
    expect(r.matchedRule).toBeNull();
  });

  it("allow with predicate matches only when predicate holds", () => {
    const e = new PermissionEngine({
      defaultMode: "ask",
      allow: ["paper.submit_order(notional<100)"],
      ask: [],
      deny: [],
    });

    expect(e.authorize("paper.submit_order", { notional: 50 }).decision).toBe("allow");
    expect(e.authorize("paper.submit_order", { notional: 1000 }).decision).toBe("ask");
  });

  it("deny rule with high-threshold predicate, ask for lower", () => {
    const e = new PermissionEngine({
      defaultMode: "ask",
      allow: [],
      ask: ["live.submit_order(notional<1000)"],
      deny: ["live.submit_order(notional>=10000)"],
    });

    expect(e.authorize("live.submit_order", { notional: 100 }).decision).toBe("ask");
    expect(e.authorize("live.submit_order", { notional: 5000 }).decision).toBe("ask"); // falls to default which is ask
    expect(e.authorize("live.submit_order", { notional: 50_000 }).decision).toBe("deny");
  });

  it("nested path in predicate", () => {
    const e = new PermissionEngine({
      defaultMode: "ask",
      allow: ["trade.create_plan(orderParams.quantity<0.05)"],
      ask: [],
      deny: [],
    });

    expect(
      e.authorize("trade.create_plan", { orderParams: { quantity: 0.01 } }).decision,
    ).toBe("allow");
    expect(
      e.authorize("trade.create_plan", { orderParams: { quantity: 0.5 } }).decision,
    ).toBe("ask"); // default
  });

  it("list() returns parsed rules grouped", () => {
    const e = new PermissionEngine({
      defaultMode: "allow",
      allow: ["paper.*"],
      ask: ["live.*"],
      deny: ["live.close_all"],
    });
    const l = e.list();
    expect(l.allow).toHaveLength(1);
    expect(l.ask).toHaveLength(1);
    expect(l.deny).toHaveLength(1);
    expect(l.defaultMode).toBe("allow");
  });
});

// ────────────────────────────────────────────────────────────────────
// mergeConfigs
// ────────────────────────────────────────────────────────────────────

describe("mergeConfigs", () => {
  it("returns empty config when given empty list", () => {
    const m = mergeConfigs([]);
    expect(m.defaultMode).toBe("ask");
    expect(m.allow).toEqual([]);
    expect(m.deny).toEqual([]);
  });

  it("later layer defaultMode wins", () => {
    const m = mergeConfigs([
      { defaultMode: "allow", allow: [], ask: [], deny: [] },
      { defaultMode: "ask", allow: [], ask: [], deny: [] },
    ]);
    expect(m.defaultMode).toBe("ask");
  });

  it("rules across layers are unioned and deduped", () => {
    const m = mergeConfigs([
      { defaultMode: "ask", allow: ["a.*", "b.*"], ask: [], deny: ["x.*"] },
      { defaultMode: "ask", allow: ["b.*", "c.*"], ask: ["y.*"], deny: ["x.*", "z.*"] },
    ]);
    expect([...m.allow].sort()).toEqual(["a.*", "b.*", "c.*"]);
    expect([...m.deny].sort()).toEqual(["x.*", "z.*"]);
    expect(m.ask).toEqual(["y.*"]);
  });
});

// ────────────────────────────────────────────────────────────────────
// DEFAULT_PERMISSIONS · ADR-0011 / ADR-0012 关键路径
// ────────────────────────────────────────────────────────────────────

describe("DEFAULT_PERMISSIONS · 关键 forbidden-path / golden-path", () => {
  const engine = new PermissionEngine(DEFAULT_PERMISSIONS);

  it("denies direct paper.submit_order_intent (forced plan/exec path)", () => {
    expect(engine.authorize("paper.submit_order_intent", { notional: 50 }).decision).toBe("deny");
  });

  it("denies live.emergency_stop_all (manual-only)", () => {
    expect(engine.authorize("live.emergency_stop_all", {}).decision).toBe("deny");
  });

  it("denies large live.submit_order", () => {
    expect(engine.authorize("live.submit_order", { notional: 50_000 }).decision).toBe("deny");
  });

  it("allows the plan-exec 5-tuple", () => {
    for (const t of [
      "trade.create_plan",
      "trade.approve_plan",
      "trade.reject_plan",
      "trade.execute_plan",
      "trade.get_plan",
    ]) {
      expect(engine.authorize(t, {}).decision).toBe("allow");
    }
  });

  it("allows the existing data + paper read tools (D-7 MVP path)", () => {
    expect(engine.authorize("data.get_bars", {}).decision).toBe("allow");
    expect(engine.authorize("data.backfill_bars", {}).decision).toBe("allow");
    expect(engine.authorize("paper.run_backtest", {}).decision).toBe("allow");
    expect(engine.authorize("paper.list_strategies", {}).decision).toBe("allow");
    expect(engine.authorize("paper.health", {}).decision).toBe("allow");
  });
});
