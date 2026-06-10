"use client";

import { motion, useReducedMotion } from "motion/react";

/**
 * Hero 纯氛围底 —— 无具象物，只铺一层会呼吸的雾光。
 *
 * 三团极淡的品牌色径向辉光缓慢漂移 / 呼吸（朱红 + 狐火 / 数据青），叠在页面
 * dot-grid + grain 纹理上 → 抽象、安静、有空气感；不是 AI-slop 的高饱和大色块
 * （色值 ≤10%、软径向落空、配纹理打散）。主题自适应；reduced-motion 下静止。
 */
export function HeroAtmosphere({ className }: { className?: string }) {
  const reduce = useReducedMotion();

  return (
    <div className={className} aria-hidden>
      {/* 雾光 A —— 朱红，右上，缓慢呼吸 */}
      <motion.div
        className="absolute rounded-full"
        style={{
          width: "44rem",
          height: "44rem",
          right: "-8%",
          top: "2%",
          background:
            "radial-gradient(circle, color-mix(in oklab, var(--seal) 9%, transparent) 0%, transparent 62%)",
        }}
        animate={
          reduce
            ? undefined
            : { x: [0, 36, 0], y: [0, -26, 0], scale: [1, 1.1, 1], opacity: [0.7, 1, 0.7] }
        }
        transition={{ duration: 19, repeat: Infinity, ease: "easeInOut" }}
      />
      {/* 雾光 B —— 狐火 / 数据青，中下，错峰呼吸 */}
      <motion.div
        className="absolute rounded-full"
        style={{
          width: "40rem",
          height: "40rem",
          right: "16%",
          bottom: "-12%",
          background:
            "radial-gradient(circle, color-mix(in oklab, var(--foxfire) 7%, transparent) 0%, transparent 60%)",
        }}
        animate={
          reduce
            ? undefined
            : { x: [0, -30, 0], y: [0, 24, 0], scale: [1.05, 1, 1.05], opacity: [0.6, 0.95, 0.6] }
        }
        transition={{ duration: 24, repeat: Infinity, ease: "easeInOut" }}
      />
      {/* 雾光 C —— 极淡青，左中，托一点文字区的深度 */}
      <motion.div
        className="absolute rounded-full"
        style={{
          width: "34rem",
          height: "34rem",
          left: "-6%",
          top: "24%",
          background:
            "radial-gradient(circle, color-mix(in oklab, var(--accent) 6%, transparent) 0%, transparent 62%)",
        }}
        animate={reduce ? undefined : { scale: [1, 1.08, 1], opacity: [0.5, 0.8, 0.5] }}
        transition={{ duration: 21, repeat: Infinity, ease: "easeInOut" }}
      />

    </div>
  );
}
