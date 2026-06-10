import * as React from "react";

import { cn } from "@/lib/cn";

interface ToriiGateProps {
  className?: string;
  /** 描线色：seal（朱红）或 copper（古铜）。默认 seal。 */
  tone?: "seal" | "copper";
  /** 描线粗细，默认 2。 */
  strokeWidth?: number;
}

/**
 * 朱红鸟居线稿 —— 神社入口意象，纯装饰（aria-hidden）。
 * 只画线不填充，吃原始变量 var(--seal)/var(--copper) 自动跟主题。
 * 用作 hero / CTAFooter 的背景门楣，永远低对比、z-0、不盖信息（DESIGN.md §3.4）。
 */
export function ToriiGate({ className, tone = "seal", strokeWidth = 2 }: ToriiGateProps) {
  const stroke = tone === "seal" ? "var(--seal)" : "var(--copper)";
  return (
    <svg
      viewBox="0 0 200 160"
      fill="none"
      stroke={stroke}
      strokeWidth={strokeWidth}
      strokeLinecap="round"
      aria-hidden="true"
      className={cn("pointer-events-none", className)}
    >
      {/* 笠木（顶横梁，带微弧） */}
      <path d="M 14 30 Q 100 16 186 30" strokeWidth={strokeWidth * 1.4} />
      {/* 島木（次横梁） */}
      <path d="M 24 42 L 176 42" />
      {/* 額束（中央短柱） */}
      <path d="M 100 42 L 100 64" />
      {/* 貫（中横梁，左右出头） */}
      <path d="M 30 64 L 170 64" />
      {/* 左柱（微内收） */}
      <path d="M 52 30 L 58 150" />
      {/* 右柱 */}
      <path d="M 148 30 L 142 150" />
    </svg>
  );
}
