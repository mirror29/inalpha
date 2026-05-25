"use client";

import { motion } from "motion/react";
import { useTranslations } from "next-intl";

import { fadeUp, stagger } from "@/lib/motion";

const NODES = [
  { id: "user", x: 80, y: 130, r: 28 },
  { id: "orchestrator", x: 280, y: 130, r: 38 },
  { id: "data", x: 520, y: 50, r: 30 },
  { id: "paper", x: 520, y: 130, r: 30 },
  { id: "research", x: 520, y: 210, r: 30 },
  { id: "strategy", x: 760, y: 130, r: 30 },
] as const;

const LINKS = [
  ["user", "orchestrator"],
  ["orchestrator", "data"],
  ["orchestrator", "paper"],
  ["orchestrator", "research"],
  ["data", "strategy"],
  ["paper", "strategy"],
  ["research", "strategy"],
  ["strategy", "user"],
] as const;

function nodeById(id: string) {
  return NODES.find((n) => n.id === id)!;
}

export function TheLoop() {
  const t = useTranslations("loop");
  const nodeLabel = useTranslations("loop.nodes");

  return (
    <section className="relative border-y border-border-subtle bg-bg-elev/20 py-24 sm:py-32">
      <div className="mx-auto max-w-6xl px-6">
        <motion.div
          initial="hidden"
          whileInView="visible"
          viewport={{ once: true, margin: "-100px" }}
          variants={stagger}
          className="space-y-4 text-center"
        >
          <motion.p
            variants={fadeUp}
            className="font-mono text-xs uppercase tracking-[0.2em] text-cyan/80"
          >
            {t("eyebrow")}
          </motion.p>
          <motion.h2
            variants={fadeUp}
            className="font-mono text-2xl text-fg sm:text-3xl"
          >
            {t("title")}
          </motion.h2>
          <motion.p
            variants={fadeUp}
            className="mx-auto max-w-2xl text-sm text-fg-muted sm:text-base"
          >
            {t("blurb")}
          </motion.p>
        </motion.div>

        <motion.div
          initial={{ opacity: 0, y: 16 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true, margin: "-100px" }}
          transition={{ duration: 0.7, delay: 0.1 }}
          className="mt-14 overflow-x-auto"
        >
          <svg
            viewBox="0 0 840 260"
            className="mx-auto block min-w-[640px] max-w-full"
            role="img"
            aria-label={t("title")}
          >
            <defs>
              <linearGradient id="link-gradient" x1="0" y1="0" x2="1" y2="0">
                <stop offset="0%" stopColor="#5fb3ff" stopOpacity="0.05" />
                <stop offset="50%" stopColor="#5fb3ff" stopOpacity="0.5" />
                <stop offset="100%" stopColor="#5fb3ff" stopOpacity="0.05" />
              </linearGradient>
            </defs>

            {LINKS.map(([from, to]) => {
              const a = nodeById(from);
              const b = nodeById(to);
              return (
                <line
                  key={`${from}-${to}`}
                  x1={a.x}
                  y1={a.y}
                  x2={b.x}
                  y2={b.y}
                  stroke="url(#link-gradient)"
                  strokeWidth="1.2"
                />
              );
            })}

            {NODES.map((n) => (
              <g key={n.id} className="pulse-glow">
                <circle
                  cx={n.x}
                  cy={n.y}
                  r={n.r}
                  fill="#0a0e1a"
                  stroke="#5fb3ff"
                  strokeWidth="1.5"
                />
                <text
                  x={n.x}
                  y={n.y + 4}
                  textAnchor="middle"
                  className="fill-fg font-mono text-[11px]"
                >
                  {nodeLabel(n.id)}
                </text>
              </g>
            ))}
          </svg>
        </motion.div>
      </div>
    </section>
  );
}
