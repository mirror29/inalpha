"use client";

import { useLocale, useTranslations } from "next-intl";

import { BroadsheetSection } from "@/components/primitives/BroadsheetSection";
import {
  DataLineagePath,
  type LineageEdge,
  type LineageNode,
} from "@/components/primitives/DataLineagePath";

/**
 * 03 — Architecture. Hub → spokes → convergence → outputs.
 * Same schematic data as the kit page; this is its product-context home.
 */
export function SystemSchematic() {
  const t = useTranslations("kernel");
  const locale = useLocale();
  const isZh = locale === "zh";

  const nodes: LineageNode[] = [
    {
      id: "you",
      index: "01",
      label: isZh ? "you" : "You",
      caption: "INPUT · ENTRY",
      description: isZh ? "对话发起任意 prompt" : "Conversational entry",
      accent: "neutral",
      x: 520,
      y: 140,
      w: 160,
      h: 70,
    },
    {
      id: "orch",
      index: "02",
      label: "Orchestrator",
      caption: "MASTRA · ROUTER",
      description: isZh
        ? "调度 + 决策记录 + 工具沙箱"
        : "Dispatches, logs, sandboxes tools",
      tags: ["hooks", "perms", "plan-exec", "MCP"],
      accent: "cyan",
      x: 240,
      y: 256,
      w: 720,
      h: 92,
    },
    {
      id: "bull",
      index: "03",
      label: "Bull",
      caption: "AGENT · LONG",
      description: isZh ? "做多论据 · 证据锚定" : "Long thesis · evidence-anchored",
      accent: "bull",
      x: 80,
      y: 384,
      w: 180,
      h: 88,
    },
    {
      id: "bear",
      index: "04",
      label: "Bear",
      caption: "AGENT · SHORT",
      description: isZh ? "做空论据 · 反方持仓" : "Short thesis · opposing stance",
      accent: "fox",
      x: 290,
      y: 384,
      w: 180,
      h: 88,
    },
    {
      id: "data",
      index: "05",
      label: "Data",
      caption: "FEED · MULTI-VENUE",
      description: isZh ? "12 venue · freshness 锚定" : "12 venues · freshness-anchored",
      accent: "neutral",
      x: 500,
      y: 384,
      w: 180,
      h: 88,
    },
    {
      id: "risk",
      index: "06",
      label: "Risk gate",
      caption: "GATE · SIZING",
      description: isZh ? "权限 · 仓位上限" : "Permission · sizing check",
      accent: "gold",
      x: 710,
      y: 384,
      w: 180,
      h: 88,
    },
    {
      id: "thesis",
      index: "07",
      label: "Thesis",
      caption: "DEBATE · CONSENSUS",
      description: isZh
        ? "对立论据汇合 · 仓位裁定"
        : "Opposing arguments converge · sized",
      accent: "cyan",
      x: 320,
      y: 508,
      w: 460,
      h: 76,
    },
    {
      id: "kernel",
      index: "08",
      label: "Kernel",
      caption: "B = P = L",
      description: isZh
        ? "回测 / 模拟 / 实盘 共一份代码"
        : "Backtest = paper = live, single codebase",
      accent: "gold",
      x: 220,
      y: 620,
      w: 240,
      h: 80,
    },
    {
      id: "record",
      index: "09",
      label: "decision_record",
      caption: "JSONL · AUDIT",
      description: isZh
        ? "每个决策落盘 · 任意回放"
        : "Append-only · replay any timestamp",
      accent: "bull",
      x: 640,
      y: 620,
      w: 240,
      h: 80,
    },
  ];

  const edges: LineageEdge[] = [
    { from: "you", to: "orch", semantic: "prompt", label: "prompt" },
    { from: "orch", to: "bull", semantic: "prompt", label: "dispatch" },
    { from: "orch", to: "bear", semantic: "prompt" },
    { from: "orch", to: "data", semantic: "data", label: "feed" },
    { from: "orch", to: "risk", semantic: "control" },
    { from: "bull", to: "thesis", semantic: "decision" },
    { from: "bear", to: "thesis", semantic: "decision" },
    { from: "data", to: "thesis", semantic: "data" },
    { from: "risk", to: "thesis", semantic: "control" },
    { from: "thesis", to: "kernel", semantic: "decision", label: "execute" },
    { from: "thesis", to: "record", semantic: "feedback", label: "append" },
    {
      from: "record",
      to: "you",
      semantic: "feedback",
      label: "audit",
      curve: "arc-right",
      fromSide: "right",
      toSide: "right",
    },
  ];

  return (
    <BroadsheetSection
      index="03"
      eyebrow="Architecture · system schematic"
      title="Hub → spokes → convergence."
      intro={t("sub")}
    >
      <DataLineagePath
        nodes={nodes}
        edges={edges}
        flowing
        meta={{
          title: "INALPHA · SYSTEM SCHEMATIC",
          rev: "0.9-D9",
          date: "2026.05.26",
          counts: "9 NODES · 12 LINKS",
        }}
      />
    </BroadsheetSection>
  );
}
