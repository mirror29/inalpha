"use client";

import {
  Activity,
  FileText,
  GitBranch,
  Layers,
  Lock,
  MessagesSquare,
  Plug,
  Webhook,
  Workflow,
} from "lucide-react";
import { motion } from "motion/react";

import { AgentBubble } from "@/components/primitives/AgentBubble";
import { BroadsheetSection } from "@/components/primitives/BroadsheetSection";
import { CodeDiff } from "@/components/primitives/CodeDiff";
import {
  DataLineagePath,
  type LineageEdge,
  type LineageNode,
} from "@/components/primitives/DataLineagePath";
import { FeatureMatrix, type FeatureItem } from "@/components/primitives/FeatureMatrix";
import { LiveBadge } from "@/components/primitives/LiveBadge";
import { StatCounter } from "@/components/primitives/StatCounter";
import { TerminalBlock } from "@/components/primitives/TerminalBlock";
import { TickerStrip } from "@/components/primitives/TickerStrip";
import { fadeUp } from "@/lib/motion";

interface KitClientProps {
  locale: string;
}

export function KitClient({ locale }: KitClientProps) {
  const isZh = locale === "zh";

  /* ── System schematic — 9 nodes, 12 edges ─────────────────── */
  const lineageNodes: LineageNode[] = [
    // Tier 0 — input (centered top)
    {
      id: "you",
      index: "01",
      label: "You",
      caption: "Input · ENTRY",
      description: isZh ? "对话发起任意 prompt" : "Conversational entry point",
      accent: "neutral",
      x: 520,
      y: 140,
      w: 160,
      h: 70,
    },
    // Tier 1 — hub (wide, spans 4 children)
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
    // Tier 2 — 4 agent columns (hub spokes)
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
    // Tier 3 — convergence
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
    // Tier 4 — outputs
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

  const lineageEdges: LineageEdge[] = [
    // Input → Hub
    { from: "you", to: "orch", semantic: "prompt", label: "prompt" },
    // Hub → 4 spokes
    { from: "orch", to: "bull", semantic: "prompt", label: "dispatch" },
    { from: "orch", to: "bear", semantic: "prompt" },
    { from: "orch", to: "data", semantic: "data", label: "feed" },
    { from: "orch", to: "risk", semantic: "control" },
    // Spokes → Convergence
    { from: "bull", to: "thesis", semantic: "decision" },
    { from: "bear", to: "thesis", semantic: "decision" },
    { from: "data", to: "thesis", semantic: "data" },
    { from: "risk", to: "thesis", semantic: "control" },
    // Convergence → Outputs
    { from: "thesis", to: "kernel", semantic: "decision", label: "execute" },
    { from: "thesis", to: "record", semantic: "feedback", label: "append" },
    // Audit feedback loop (arcs around to the right)
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

  /* ── FeatureMatrix data ───────────────────────────────────── */
  const enFeatures: FeatureItem[] = [
    {
      icon: MessagesSquare,
      title: "Multi-agent debate",
      description: "Bull, Bear, and Risk agents hold opposing positions with distinct toolsets.",
      caption: "1st-class",
      accent: "cyan",
    },
    {
      icon: Layers,
      title: "Unified kernel",
      description: "One strategy codebase runs as backtest, paper, and live with identical behavior.",
      caption: "= = =",
      accent: "bull",
    },
    {
      icon: FileText,
      title: "Decision records",
      description: "Every routing, sizing, and risk verdict appended to JSONL. Replay any timestamp.",
      caption: "JSONL",
      accent: "gold",
    },
    {
      icon: Webhook,
      title: "Hooks",
      description: "Middleware runs on every tool call — observability and policy in one place.",
      accent: "cyan",
    },
    {
      icon: Lock,
      title: "Permissions",
      description: "Role-scoped tool access. Research reads news; only risk can place orders.",
      accent: "fox",
    },
    {
      icon: Workflow,
      title: "Plan-exec",
      description: "Plan first, execute once, no runaway loops. One-shot tokens per action.",
      accent: "cyan",
    },
    {
      icon: Plug,
      title: "MCP native",
      description: "Model Context Protocol plugs in tools without hand-rolled glue code.",
      accent: "gold",
    },
    {
      icon: GitBranch,
      title: "Subagent isolation",
      description: "Risk and review live in isolated subagents with their own context windows.",
      accent: "bull",
    },
    {
      icon: Activity,
      title: "Freshness anchored",
      description: "Bars and news default to `fresh=True`. Stale numbers never pass as insight.",
      caption: "fresh=True",
      accent: "fox",
    },
  ];

  const zhFeatures: FeatureItem[] = [
    { icon: MessagesSquare, title: "多 agent 辩论", description: "Bull / Bear / Risk 三个 agent 立场对立、工具集不同、决策可追。", caption: "一级公民", accent: "cyan" },
    { icon: Layers, title: "统一内核", description: "一份策略代码跑回测、模拟、实盘——三者表现必须一致。", caption: "= = =", accent: "bull" },
    { icon: FileText, title: "决策记录", description: "每次路由、定仓、风控判定追加到 JSONL。任意时间点可回放。", caption: "JSONL", accent: "gold" },
    { icon: Webhook, title: "Hooks", description: "每个 tool call 走中间件——可观测 + 策略 同一处理。", accent: "cyan" },
    { icon: Lock, title: "Permissions", description: "工具访问按角色 scope。research 只读新闻，只有 risk 能下单。", accent: "fox" },
    { icon: Workflow, title: "Plan-exec", description: "先 plan 后 exec，避免失控回路。每个动作走 one-shot token。", accent: "cyan" },
    { icon: Plug, title: "MCP 原生", description: "Model Context Protocol 接入工具，无需手写胶水代码。", accent: "gold" },
    { icon: GitBranch, title: "子 agent 隔离", description: "风控与复盘住在独立 subagent 里，有自己的 context window。", accent: "bull" },
    { icon: Activity, title: "Freshness 锚定", description: "K 线与新闻默认 fresh=True。过期数据绝不当洞察传。", caption: "fresh=True", accent: "fox" },
  ];

  const features = isZh ? zhFeatures : enFeatures;

  const tickerItems = [
    "INALPHA",
    "PRIMITIVE LEDGER",
    "D-9",
    "REV 0.9",
    "2026.05.26",
    "9 PRIMITIVES",
    "12 LINKS",
    "ALPHA QUALITY",
    "AGPL-3.0",
    "BACKTEST = PAPER = LIVE",
    "AGENTS FIRST-CLASS",
  ];

  return (
    <div className="relative min-h-screen grain bg-bg text-fg">
      {/* Top ticker strip */}
      <TickerStrip items={tickerItems} />

      {/* ── Header block ───────────────────────────────────────── */}
      <header className="relative border-b border-fg/12">
        <div className="mx-auto max-w-7xl px-6 pb-20 pt-16 md:px-12 md:pb-28 md:pt-24">
          <div className="grid grid-cols-12 gap-6">
            <div className="col-span-12 md:col-span-7">
              <motion.div
                initial="hidden"
                animate="visible"
                variants={{
                  hidden: {},
                  visible: { transition: { staggerChildren: 0.08, delayChildren: 0.05 } },
                }}
                className="space-y-6"
              >
                <motion.p
                  variants={fadeUp}
                  className="font-mono text-[11px] uppercase tracking-[0.32em] text-fg-muted/80"
                >
                  ── [01—09] · DESIGN.md §7.2 · visual acceptance
                </motion.p>
                <motion.h1
                  variants={fadeUp}
                  className="display-italic text-fg leading-[0.86]"
                  style={{
                    fontSize: "clamp(4rem, 11.5vw, 11rem)",
                    fontWeight: 300,
                  }}
                >
                  Primitive
                  <br />
                  <span className="text-cyan">ledger</span>
                  <span className="text-fg/30">.</span>
                </motion.h1>
                <motion.p
                  variants={fadeUp}
                  className="max-w-[54ch] text-[16px] leading-relaxed text-fg-muted"
                >
                  {isZh
                    ? "每个 primitive 渲染一次代表性 props。对照 DESIGN.md §7.2 验收。若有漂移——先修 primitive，不要 fork 一次性变体。"
                    : "Every primitive renders once with representative props. Verify against DESIGN.md §7.2. If anything drifts — fix the primitive, never fork a one-off variant."}
                </motion.p>
              </motion.div>
            </div>

            {/* Right meta column — engineering drawing title block */}
            <motion.aside
              initial={{ opacity: 0, y: 10 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.55, delay: 0.4 }}
              className="col-span-12 md:col-span-4 md:col-start-9"
            >
              <dl className="grid grid-cols-2 gap-x-6 gap-y-3 border border-fg/12 bg-bg-deep/60 p-5 font-mono text-[11px] uppercase tracking-[0.22em]">
                <dt className="text-fg-muted/60">project</dt>
                <dd className="text-fg">Inalpha</dd>
                <dt className="text-fg-muted/60">rev</dt>
                <dd className="text-fg">0.9 · D-9</dd>
                <dt className="text-fg-muted/60">date</dt>
                <dd className="text-fg">2026.05.26</dd>
                <dt className="text-fg-muted/60">stack</dt>
                <dd className="text-fg">next 16 · tw 4</dd>
                <dt className="text-fg-muted/60">font</dt>
                <dd className="text-fg">fraunces · geist</dd>
                <dt className="text-fg-muted/60">status</dt>
                <dd className="flex items-center gap-2 text-bull">
                  <span className="size-1.5 rounded-full bg-bull caret-blink" />
                  live
                </dd>
              </dl>

              <div className="mt-5 flex flex-wrap gap-2">
                <LiveBadge label="alpha quality" />
                <LiveBadge tint="cyan" label="AGPL-3.0" />
              </div>
            </motion.aside>
          </div>
        </div>
      </header>

      <main className="relative mx-auto max-w-7xl space-y-32 px-6 py-24 md:space-y-40 md:px-12">
        {/* ── 01 · System schematic ─────────────────────────────── */}
        <BroadsheetSection
          index="01"
          eyebrow={isZh ? "ARCHITECTURE · 系统数据流" : "ARCHITECTURE · System data flow"}
          title={isZh ? "Hub → spokes → convergence." : "Hub → spokes → convergence."}
          intro={
            isZh
              ? "一份对话进入 orchestrator，扇出至四个独立 agent。Bull / Bear / Data / Risk 同时给出证据，汇合到 thesis 节点裁定仓位；输出走统一内核执行，同时落盘 decision_record，闭环回到调用者作为审计来源。"
              : "A single prompt enters the orchestrator and fans out to four independent agents. Bull / Bear / Data / Risk surface evidence in parallel, converging at the thesis node where position is sized; output runs through the unified kernel while every decision is appended to decision_record, closing the audit loop back to you."
          }
        >
          <DataLineagePath
            nodes={lineageNodes}
            edges={lineageEdges}
            flowing
            meta={{
              title: "INALPHA · SYSTEM SCHEMATIC",
              rev: "0.9-D9",
              date: "2026.05.26",
              counts: "9 NODES · 12 LINKS",
            }}
          />
        </BroadsheetSection>

        {/* ── 02 · Capabilities (FeatureMatrix) ────────────────── */}
        <BroadsheetSection
          index="02"
          eyebrow={isZh ? "CAPABILITY · 工程绑带" : "CAPABILITY · engineering surface"}
          title={isZh ? "Nine capabilities, no marketing." : "Nine capabilities, no marketing."}
          intro={
            isZh
              ? "技术亮点不是宣言体，是可被审计的实现。Agent 一级公民 + 工程纪律落地为可调用的 primitive：hooks / permissions / plan-exec / MCP / subagent / swarm / freshness。"
              : "Capabilities aren't claims — they're auditable primitives. Multi-agent debate, unified kernel, decision records, hooks, permissions, plan-exec, MCP, subagent isolation, freshness anchoring."
          }
        >
          <FeatureMatrix items={features} columns={3} />
        </BroadsheetSection>

        {/* ── 03 · Agent bubbles ───────────────────────────────── */}
        <BroadsheetSection
          index="03"
          eyebrow="ROLE-TINTED OUTPUT · agentBubble"
          title={isZh ? "Four roles, one workspace." : "Four roles, one workspace."}
          intro={
            isZh
              ? "每个 agent 用其立场色定调，状态点显式：idle / thinking / done。审稿不靠语义猜测，靠视觉契约。"
              : "Each agent is tinted by stance. The lifecycle dot is explicit — idle / thinking / done. Reviewers read posture before they read prose."
          }
        >
          <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
            <AgentBubble role="research" status="done" label="research">
              Pulled 4 feeds. DXY 105.6 · BTC 24h vol +18% · FOMC priced 20bp.
            </AgentBubble>
            <AgentBubble role="bull" status="thinking">
              Macro cut already discounted in front-month basis. Edge +0.4σ.
            </AgentBubble>
            <AgentBubble role="bear" status="thinking">
              Funding rate inverted on 3 exchanges; bull positioning crowded.
            </AgentBubble>
            <AgentBubble role="risk" status="idle">
              Awaiting consensus output.
            </AgentBubble>
          </div>
        </BroadsheetSection>

        {/* ── 04 · Terminal blocks ─────────────────────────────── */}
        <BroadsheetSection
          index="04"
          eyebrow="CONFIG · terminalBlock"
          title={isZh ? "Permissions as text. Records as JSONL." : "Permissions as text. Records as JSONL."}
          intro={
            isZh
              ? "agent 能调什么不能调什么 — 全在版本控制里。决策落盘也是 JSONL，可 diff、可 grep、可 replay。"
              : "What an agent can or cannot call lives in version control. So does every decision — diffable, greppable, replayable."
          }
        >
          <div className="grid gap-5 md:grid-cols-2">
            <TerminalBlock
              prompt="$"
              caption="permissions.yaml"
              content={[
                "research:",
                "  allow:",
                "    - data.get_bars",
                "    - data.get_news",
                "  deny:",
                "    - paper.place_order",
              ]}
            />
            <TerminalBlock
              prompt="$ inalpha>"
              caption="decision_record.jsonl"
              typewriter
              content={`{"agent":"portfolio","action":"long","size":0.4,"rationale":"bull edge -0.3"}`}
            />
          </div>
        </BroadsheetSection>

        {/* ── 05 · CodeDiff ────────────────────────────────────── */}
        <BroadsheetSection
          index="05"
          eyebrow="UNIFIED KERNEL · codeDiff"
          title={isZh ? "Backtest equals live. One line." : "Backtest equals live. One line."}
          intro={
            isZh
              ? "策略代码不为模式分叉。换内核就只换内核 — 引擎签名相同，行为契约相同。"
              : "Strategy code never branches on mode. Swap kernels, not behavior — the engine signature is identical."
          }
        >
          <CodeDiff
            beforeLabel="backtest"
            afterLabel="live"
            before={[
              "from inalpha_paper import BacktestEngine",
              "",
              "engine = BacktestEngine(bars=bars_2024)",
              "strategy.run(engine)",
            ]}
            after={[
              "from inalpha_paper import LiveEngine",
              "",
              "engine = LiveEngine(broker=ibkr)",
              "strategy.run(engine)",
            ]}
          />
        </BroadsheetSection>

        {/* ── 06 · Stat counters ──────────────────────────────── */}
        <BroadsheetSection
          index="06"
          eyebrow="METRICS · statCounter"
          title={isZh ? "Numbers that scroll in." : "Numbers that scroll in."}
          intro={
            isZh
              ? "数字进入视野时滚动入场——给读者一个微弱信号：这数据是新的、是被观测的。"
              : "Numbers ease up as they enter the viewport — a small signal that the data is current and observed."
          }
        >
          <div className="flex flex-wrap items-end gap-x-12 gap-y-6 border-y border-fg/12 py-10">
            <Stat target={142} label="stars" accent="text-cyan" />
            <Stat target={23} label="contributors" />
            <Stat target={487} label="commits" />
            <Stat target={12} label="markets" accent="text-gold" />
          </div>
        </BroadsheetSection>

        {/* ── Footer ────────────────────────────────────────────── */}
        <footer className="border-t border-fg/12 pt-8 pb-16">
          <div className="grid grid-cols-12 gap-6 font-mono text-[10px] uppercase tracking-[0.26em] text-fg-muted/70">
            <div className="col-span-6 md:col-span-3">
              <p className="text-fg/40">file</p>
              <p className="mt-1 text-fg-muted">apps/web/src/app/[locale]/kit/</p>
            </div>
            <div className="col-span-6 md:col-span-3">
              <p className="text-fg/40">spec</p>
              <p className="mt-1 text-fg-muted">DESIGN.md §7.2</p>
            </div>
            <div className="col-span-6 md:col-span-3">
              <p className="text-fg/40">primitives</p>
              <p className="mt-1 text-fg-muted">8 · 0 type errors</p>
            </div>
            <div className="col-span-6 md:col-span-3">
              <p className="text-fg/40">rev</p>
              <p className="mt-1 text-fg-muted">
                0.9-D9 · {new Date().getFullYear()}.05.26
              </p>
            </div>
          </div>
        </footer>
      </main>
    </div>
  );
}

function Stat({
  target,
  label,
  accent = "text-fg",
}: {
  target: number;
  label: string;
  accent?: string;
}) {
  return (
    <div className="flex flex-col gap-1.5">
      <StatCounter
        target={target}
        className={`font-mono leading-none tabular-nums text-[clamp(2.25rem,4.4vw,3.75rem)] tracking-tight ${accent}`}
      />
      <span className="font-mono text-[10px] uppercase tracking-[0.26em] text-fg-muted/70">
        ── {label}
      </span>
    </div>
  );
}
