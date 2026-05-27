"use client";

import * as React from "react";
import { motion, useInView, useReducedMotion } from "motion/react";

import { cn } from "@/lib/cn";

/**
 * SYSTEM SCHEMATIC — see DESIGN.md §7.2.7
 *
 * Renders the Inalpha agent pipeline as an engineering-drawing schematic
 * (hub → spokes → convergence → outputs + audit arc). Pure SVG. No
 * foreignObject, no glass cards, no drop shadows — hairlines only.
 *
 * The drawing area is fixed-coordinate (1200 x 760). Callers pass nodes
 * with explicit (x, y, w, h) plus semantic accent and edges with explicit
 * source/target sides and optional `arc-around` routing for the audit loop.
 */

export type Accent = "cyan" | "fox" | "gold" | "bull" | "neutral";
export type Semantic = "prompt" | "data" | "decision" | "feedback" | "control";
export type Side = "top" | "right" | "bottom" | "left";

export interface LineageNode {
  id: string;
  index?: string; // e.g. "01"
  label: string;
  caption?: string; // small one-line mono caption under the label
  description?: string; // optional sentence
  tags?: string[]; // shown as hairline chips inside wide boxes
  accent?: Accent;
  x: number;
  y: number;
  w?: number;
  h?: number;
}

export interface LineageEdge {
  from: string;
  to: string;
  label?: string;
  semantic?: Semantic;
  fromSide?: Side;
  toSide?: Side;
  /** Special routing for the audit feedback loop. */
  curve?: "auto" | "arc-right";
}

interface DataLineagePathProps {
  nodes: LineageNode[];
  edges: LineageEdge[];
  /** Reverse-engineering metadata shown in the title block (top-right). */
  meta?: {
    title: string;
    rev?: string;
    date?: string;
    counts?: string; // e.g. "9 NODES · 12 LINKS"
  };
  flowing?: boolean;
  className?: string;
}

const VIEW_W = 1200;
const VIEW_H = 760;
const DEFAULT_W = 180;
const DEFAULT_H = 84;

const accentColor: Record<Accent, string> = {
  cyan: "#5fb3ff",
  fox: "#c8463c",
  gold: "#d4a744",
  bull: "#4ade80",
  neutral: "#9ba3b4",
};

const semanticStyle: Record<
  Semantic,
  { color: string; dash?: string; label: string }
> = {
  prompt: { color: "#5fb3ff", label: "PROMPT" },
  data: { color: "#9ba3b4", dash: "4 4", label: "DATA" },
  decision: { color: "#d4a744", label: "DECISION" },
  feedback: { color: "#4ade80", dash: "2 6", label: "FEEDBACK" },
  control: { color: "#c8463c", label: "CONTROL" },
};

interface Anchor {
  x: number;
  y: number;
}

function anchorOf(n: LineageNode, side: Side): Anchor {
  const w = n.w ?? DEFAULT_W;
  const h = n.h ?? DEFAULT_H;
  switch (side) {
    case "top":
      return { x: n.x + w / 2, y: n.y };
    case "bottom":
      return { x: n.x + w / 2, y: n.y + h };
    case "left":
      return { x: n.x, y: n.y + h / 2 };
    case "right":
      return { x: n.x + w, y: n.y + h / 2 };
  }
}

function defaultSides(from: LineageNode, to: LineageNode): [Side, Side] {
  const fy = from.y + (from.h ?? DEFAULT_H) / 2;
  const ty = to.y + (to.h ?? DEFAULT_H) / 2;
  if (Math.abs(ty - fy) > Math.abs(to.x - from.x)) {
    return ty > fy ? ["bottom", "top"] : ["top", "bottom"];
  }
  return to.x > from.x ? ["right", "left"] : ["left", "right"];
}

function buildPath(
  from: Anchor,
  to: Anchor,
  fromSide: Side,
  toSide: Side,
  curve: "auto" | "arc-right",
): string {
  if (curve === "arc-right") {
    const sweepX = Math.max(from.x, to.x) + 220;
    return `M ${from.x} ${from.y} C ${sweepX} ${from.y}, ${sweepX} ${to.y}, ${to.x} ${to.y}`;
  }
  // Smooth cubic. Pull control handles in the direction of each anchor's side.
  const off = 60;
  const dirFrom = sideOffset(fromSide, off);
  const dirTo = sideOffset(toSide, off);
  const c1 = { x: from.x + dirFrom.dx, y: from.y + dirFrom.dy };
  const c2 = { x: to.x + dirTo.dx, y: to.y + dirTo.dy };
  return `M ${from.x} ${from.y} C ${c1.x} ${c1.y}, ${c2.x} ${c2.y}, ${to.x} ${to.y}`;
}

function sideOffset(side: Side, off: number): { dx: number; dy: number } {
  switch (side) {
    case "top":
      return { dx: 0, dy: -off };
    case "bottom":
      return { dx: 0, dy: off };
    case "left":
      return { dx: -off, dy: 0 };
    case "right":
      return { dx: off, dy: 0 };
  }
}

export function DataLineagePath({
  nodes,
  edges,
  meta,
  flowing = true,
  className,
}: DataLineagePathProps) {
  const reduced = useReducedMotion();
  const animateFlow = flowing && !reduced;
  const containerRef = React.useRef<HTMLDivElement | null>(null);
  const inView = useInView(containerRef, { once: true, margin: "-80px" });
  const byId = React.useMemo(
    () => new Map(nodes.map((n) => [n.id, n])),
    [nodes],
  );

  // Resolved edges with anchors and paths
  const resolved = React.useMemo(() => {
    return edges.map((e) => {
      const from = byId.get(e.from);
      const to = byId.get(e.to);
      if (!from || !to) return null;
      const [defFrom, defTo] = defaultSides(from, to);
      const fromSide = e.fromSide ?? defFrom;
      const toSide = e.toSide ?? defTo;
      const a = anchorOf(from, fromSide);
      const b = anchorOf(to, toSide);
      const sem = e.semantic ?? "prompt";
      const path = buildPath(a, b, fromSide, toSide, e.curve ?? "auto");
      return { e, a, b, sem, path };
    });
  }, [edges, byId]);

  const nodeDelay = (i: number) => 0.08 + i * 0.06;
  const edgeDelay = (i: number) => 0.08 + nodes.length * 0.06 + 0.05 + i * 0.04;

  return (
    <div
      ref={containerRef}
      className={cn(
        "relative grain overflow-hidden border border-fg/12 bg-bg-deep",
        className,
      )}
    >
      {/* Drafting grid behind the diagram */}
      <div
        aria-hidden
        className="pointer-events-none absolute inset-0 hairline-grid opacity-50 [mask-image:radial-gradient(ellipse_at_center,black_55%,transparent_92%)]"
      />

      <svg
        viewBox={`0 0 ${VIEW_W} ${VIEW_H}`}
        className="block h-auto w-full"
        role="img"
        aria-label="Inalpha system schematic"
      >
        <defs>
          {/* Arrow markers per semantic */}
          {(Object.entries(semanticStyle) as [Semantic, { color: string }][]).map(
            ([key, val]) => (
              <marker
                key={key}
                id={`arrow-${key}`}
                viewBox="0 0 10 10"
                refX="9.5"
                refY="5"
                markerWidth="5"
                markerHeight="5"
                orient="auto-start-reverse"
              >
                <path d="M 0 0 L 10 5 L 0 10 z" fill={val.color} />
              </marker>
            ),
          )}
        </defs>

        {/* ── Drawing frame ─────────────────────────────────────────── */}
        <rect
          x={28}
          y={28}
          width={VIEW_W - 56}
          height={VIEW_H - 56}
          fill="none"
          stroke="rgba(245,245,247,0.10)"
          strokeWidth={1}
        />
        {/* Corner registration marks */}
        {[
          [28, 28],
          [VIEW_W - 28, 28],
          [28, VIEW_H - 28],
          [VIEW_W - 28, VIEW_H - 28],
        ].map(([cx, cy], i) => (
          <CrosshairMark key={i} cx={cx} cy={cy} delay={0.04 + i * 0.05} inView={inView} />
        ))}

        {/* ── Title block (top-right) ───────────────────────────────── */}
        {meta ? (
          <TitleBlock
            x={VIEW_W - 332}
            y={48}
            w={304}
            h={68}
            meta={meta}
            inView={inView}
          />
        ) : null}

        {/* ── Coordinate ruler ticks down the left edge ────────────── */}
        <RulerTicks x={28} y0={140} y1={VIEW_H - 80} step={80} inView={inView} />

        {/* ── Edges (drawn before nodes so they sit beneath) ──────── */}
        {resolved.map((r, i) => {
          if (!r) return null;
          const sm = semanticStyle[r.sem];
          const dashAnimated = animateFlow && !sm.dash;
          const labelMid = midOfPath(r.a, r.b, r.e.curve ?? "auto");
          return (
            <g key={`edge-${i}`}>
              {/* Anchor port at source */}
              <motion.circle
                cx={r.a.x}
                cy={r.a.y}
                r={3}
                fill={sm.color}
                initial={{ scale: 0, opacity: 0 }}
                animate={inView ? { scale: 1, opacity: 1 } : undefined}
                transition={{ duration: 0.25, delay: edgeDelay(i) }}
              />
              {/* The line. No pathLength animation — keeps markerEnd anchored. */}
              <motion.path
                d={r.path}
                fill="none"
                stroke={sm.color}
                strokeWidth={1.1}
                strokeLinecap="round"
                strokeDasharray={dashAnimated ? "5 7" : sm.dash}
                markerEnd={`url(#arrow-${r.sem})`}
                initial={{ opacity: 0 }}
                animate={
                  inView
                    ? animateFlow
                      ? {
                          opacity: 0.88,
                          strokeDashoffset: sm.dash ? [0, -24] : [0, -48],
                        }
                      : { opacity: 0.88 }
                    : undefined
                }
                transition={
                  animateFlow
                    ? {
                        opacity: { duration: 0.45, delay: edgeDelay(i) },
                        strokeDashoffset: {
                          duration: 1.4,
                          ease: "linear",
                          repeat: Infinity,
                          delay: edgeDelay(i) + 0.2,
                        },
                      }
                    : { opacity: { duration: 0.45, delay: edgeDelay(i) } }
                }
              />
              {/* Anchor port at target */}
              <motion.circle
                cx={r.b.x}
                cy={r.b.y}
                r={3}
                fill={sm.color}
                initial={{ scale: 0, opacity: 0 }}
                animate={inView ? { scale: 1, opacity: 1 } : undefined}
                transition={{ duration: 0.25, delay: edgeDelay(i) + 0.05 }}
              />
              {/* Edge label */}
              {r.e.label ? (
                <motion.g
                  initial={{ opacity: 0 }}
                  animate={inView ? { opacity: 1 } : undefined}
                  transition={{ duration: 0.4, delay: edgeDelay(i) + 0.15 }}
                >
                  <rect
                    x={labelMid.x - r.e.label.length * 3 - 6}
                    y={labelMid.y - 8}
                    width={r.e.label.length * 6 + 12}
                    height={14}
                    fill="#0a0e1a"
                  />
                  <text
                    x={labelMid.x}
                    y={labelMid.y + 2}
                    textAnchor="middle"
                    fontFamily="var(--font-mono)"
                    fontSize={9}
                    letterSpacing="0.18em"
                    fill={sm.color}
                    style={{ textTransform: "uppercase" }}
                  >
                    {r.e.label}
                  </text>
                </motion.g>
              ) : null}
            </g>
          );
        })}

        {/* ── Nodes ────────────────────────────────────────────────── */}
        {nodes.map((n, i) => (
          <SchematicNode key={n.id} node={n} delay={nodeDelay(i)} inView={inView} />
        ))}

        {/* ── Bottom legend ────────────────────────────────────────── */}
        <LegendStrip x={48} y={VIEW_H - 50} inView={inView} />

        {/* ── Bottom-right meta ─────────────────────────────────────── */}
        <g transform={`translate(${VIEW_W - 220}, ${VIEW_H - 56})`}>
          <text
            fontFamily="var(--font-mono)"
            fontSize={9}
            letterSpacing="0.22em"
            fill="rgba(245,245,247,0.30)"
          >
            <tspan x={0} y={0} style={{ textTransform: "uppercase" }}>
              ◯ SCHEMATIC · NOT TO SCALE
            </tspan>
            <tspan x={0} y={14} style={{ textTransform: "uppercase" }}>
              ↳ FOR REFERENCE ONLY
            </tspan>
          </text>
        </g>
      </svg>
    </div>
  );
}

/* ────────────────────────────────────────────────────────────────────── */

function midOfPath(
  a: Anchor,
  b: Anchor,
  curve: "auto" | "arc-right",
): { x: number; y: number } {
  if (curve === "arc-right") {
    return { x: Math.max(a.x, b.x) + 150, y: (a.y + b.y) / 2 };
  }
  return { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 };
}

function CrosshairMark({
  cx,
  cy,
  delay,
  inView,
}: {
  cx: number;
  cy: number;
  delay: number;
  inView: boolean;
}) {
  return (
    <motion.g
      initial={{ opacity: 0 }}
      animate={inView ? { opacity: 1 } : undefined}
      transition={{ duration: 0.35, delay }}
    >
      <line
        x1={cx - 8}
        y1={cy}
        x2={cx + 8}
        y2={cy}
        stroke="rgba(245,245,247,0.35)"
        strokeWidth={1}
      />
      <line
        x1={cx}
        y1={cy - 8}
        x2={cx}
        y2={cy + 8}
        stroke="rgba(245,245,247,0.35)"
        strokeWidth={1}
      />
      <circle cx={cx} cy={cy} r={2.5} fill="none" stroke="rgba(245,245,247,0.35)" />
    </motion.g>
  );
}

function TitleBlock({
  x,
  y,
  w,
  h,
  meta,
  inView,
}: {
  x: number;
  y: number;
  w: number;
  h: number;
  meta: NonNullable<DataLineagePathProps["meta"]>;
  inView: boolean;
}) {
  return (
    <motion.g
      transform={`translate(${x}, ${y})`}
      initial={{ opacity: 0 }}
      animate={inView ? { opacity: 1 } : undefined}
      transition={{ duration: 0.5, delay: 0.06 }}
    >
      <rect
        x={0}
        y={0}
        width={w}
        height={h}
        fill="rgba(10,14,26,0.85)"
        stroke="rgba(245,245,247,0.18)"
        strokeWidth={1}
      />
      <text
        x={14}
        y={20}
        fontFamily="var(--font-mono)"
        fontSize={10}
        letterSpacing="0.26em"
        fill="rgba(245,245,247,0.85)"
        style={{ textTransform: "uppercase" }}
      >
        {meta.title}
      </text>
      <line
        x1={14}
        y1={28}
        x2={w - 14}
        y2={28}
        stroke="rgba(245,245,247,0.18)"
        strokeWidth={1}
      />
      <text
        x={14}
        y={44}
        fontFamily="var(--font-mono)"
        fontSize={9}
        letterSpacing="0.22em"
        fill="rgba(155,163,180,0.95)"
        style={{ textTransform: "uppercase" }}
      >
        REV {meta.rev ?? "0.9"}
      </text>
      <text
        x={w - 14}
        y={44}
        textAnchor="end"
        fontFamily="var(--font-mono)"
        fontSize={9}
        letterSpacing="0.22em"
        fill="rgba(155,163,180,0.95)"
        style={{ textTransform: "uppercase" }}
      >
        {meta.date ?? ""}
      </text>
      {meta.counts ? (
        <text
          x={14}
          y={60}
          fontFamily="var(--font-mono)"
          fontSize={9}
          letterSpacing="0.22em"
          fill="rgba(95,179,255,0.85)"
          style={{ textTransform: "uppercase" }}
        >
          {meta.counts}
        </text>
      ) : null}
    </motion.g>
  );
}

function RulerTicks({
  x,
  y0,
  y1,
  step,
  inView,
}: {
  x: number;
  y0: number;
  y1: number;
  step: number;
  inView: boolean;
}) {
  const ticks: number[] = [];
  for (let y = y0; y <= y1; y += step) ticks.push(y);
  return (
    <motion.g
      initial={{ opacity: 0 }}
      animate={inView ? { opacity: 1 } : undefined}
      transition={{ duration: 0.4, delay: 0.12 }}
    >
      <line
        x1={x}
        y1={y0 - 6}
        x2={x}
        y2={y1 + 6}
        stroke="rgba(245,245,247,0.12)"
        strokeWidth={1}
      />
      {ticks.map((y, i) => (
        <g key={i}>
          <line
            x1={x}
            y1={y}
            x2={x + (i % 2 === 0 ? 8 : 4)}
            y2={y}
            stroke="rgba(245,245,247,0.25)"
            strokeWidth={1}
          />
          {i % 2 === 0 ? (
            <text
              x={x + 14}
              y={y + 3}
              fontFamily="var(--font-mono)"
              fontSize={8}
              letterSpacing="0.2em"
              fill="rgba(245,245,247,0.25)"
            >
              {String((i + 1) * 100).padStart(3, "0")}
            </text>
          ) : null}
        </g>
      ))}
    </motion.g>
  );
}

function LegendStrip({
  x,
  y,
  inView,
}: {
  x: number;
  y: number;
  inView: boolean;
}) {
  const entries = Object.entries(semanticStyle) as [
    Semantic,
    { color: string; label: string; dash?: string },
  ][];
  let cursor = x;
  return (
    <motion.g
      initial={{ opacity: 0 }}
      animate={inView ? { opacity: 1 } : undefined}
      transition={{ duration: 0.45, delay: 0.6 }}
    >
      <text
        x={x}
        y={y - 4}
        fontFamily="var(--font-mono)"
        fontSize={9}
        letterSpacing="0.26em"
        fill="rgba(245,245,247,0.45)"
        style={{ textTransform: "uppercase" }}
      >
        LINK · KEY
      </text>
      <g transform={`translate(${x + 80}, ${y - 4})`}>
        {entries.map(([key, val], i) => {
          const gap = 26;
          const lineLen = 26;
          const labelGap = 6;
          const labelWidth = val.label.length * 6.4;
          const cellWidth = lineLen + labelGap + labelWidth + gap;
          const tx = i * 0;
          const g = (
            <g key={key} transform={`translate(${cursor - x - 80}, 0)`}>
              <line
                x1={0}
                y1={0}
                x2={lineLen}
                y2={0}
                stroke={val.color}
                strokeWidth={1.3}
                strokeDasharray={val.dash}
              />
              <text
                x={lineLen + labelGap}
                y={3}
                fontFamily="var(--font-mono)"
                fontSize={9}
                letterSpacing="0.22em"
                fill={val.color}
                style={{ textTransform: "uppercase" }}
              >
                {val.label}
              </text>
            </g>
          );
          cursor += cellWidth;
          return g;
        })}
      </g>
    </motion.g>
  );
}

function SchematicNode({
  node,
  delay,
  inView,
}: {
  node: LineageNode;
  delay: number;
  inView: boolean;
}) {
  const accent = accentColor[node.accent ?? "neutral"];
  const w = node.w ?? DEFAULT_W;
  const h = node.h ?? DEFAULT_H;
  return (
    <motion.g
      transform={`translate(${node.x}, ${node.y})`}
      initial={{ opacity: 0 }}
      animate={inView ? { opacity: 1 } : undefined}
      transition={{ duration: 0.42, delay }}
    >
      {/* Body */}
      <rect
        x={0}
        y={0}
        width={w}
        height={h}
        fill="rgba(10,14,26,0.92)"
        stroke="rgba(245,245,247,0.16)"
        strokeWidth={1}
      />
      {/* Accent tick — left edge */}
      <line x1={0} y1={0} x2={0} y2={h} stroke={accent} strokeWidth={2} />
      {/* Top-right index */}
      {node.index ? (
        <text
          x={w - 8}
          y={14}
          textAnchor="end"
          fontFamily="var(--font-mono)"
          fontSize={9}
          letterSpacing="0.28em"
          fill="rgba(245,245,247,0.35)"
        >
          {node.index}
        </text>
      ) : null}
      {/* Label */}
      <text
        x={14}
        y={26}
        fontFamily="var(--font-mono)"
        fontSize={13}
        fontWeight={500}
        letterSpacing="0.16em"
        fill="#f5f5f7"
        style={{ textTransform: "uppercase" }}
      >
        {node.label}
      </text>
      {/* Caption — small mono caps */}
      {node.caption ? (
        <text
          x={14}
          y={42}
          fontFamily="var(--font-mono)"
          fontSize={9}
          letterSpacing="0.2em"
          fill={accent}
          style={{ textTransform: "uppercase" }}
        >
          {node.caption}
        </text>
      ) : null}
      {/* Description — sans */}
      {node.description ? (
        <text
          x={14}
          y={node.caption ? 58 : 44}
          fontFamily="var(--font-sans)"
          fontSize={11}
          fill="rgba(155,163,180,0.95)"
        >
          {node.description}
        </text>
      ) : null}
      {/* Tag chips (only if box is wide enough) */}
      {node.tags && node.tags.length > 0 ? (
        <NodeChips
          x={14}
          y={h - 18}
          tags={node.tags}
          accent={accent}
          maxWidth={w - 28}
        />
      ) : null}
    </motion.g>
  );
}

function NodeChips({
  x,
  y,
  tags,
  accent,
  maxWidth,
}: {
  x: number;
  y: number;
  tags: string[];
  accent: string;
  maxWidth: number;
}) {
  let cursor = 0;
  const CHIP_PAD_X = 8;
  const FONT = 9;
  const HEIGHT = 13;
  return (
    <g transform={`translate(${x}, ${y})`}>
      {tags.map((t, i) => {
        const w = t.length * 6.4 + CHIP_PAD_X * 2;
        if (cursor + w > maxWidth) return null;
        const tx = cursor;
        cursor += w + 6;
        return (
          <g key={i} transform={`translate(${tx}, 0)`}>
            <rect
              x={0}
              y={0}
              width={w}
              height={HEIGHT}
              fill="none"
              stroke={accent}
              strokeOpacity={0.38}
              strokeWidth={1}
            />
            <text
              x={w / 2}
              y={9.5}
              textAnchor="middle"
              fontFamily="var(--font-mono)"
              fontSize={FONT}
              letterSpacing="0.18em"
              fill={accent}
              style={{ textTransform: "uppercase" }}
            >
              {t}
            </text>
          </g>
        );
      })}
    </g>
  );
}
