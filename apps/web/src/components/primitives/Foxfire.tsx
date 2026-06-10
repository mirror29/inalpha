import * as React from "react";

import { cn } from "@/lib/cn";

interface Spark {
  /** 相对父容器的定位（CSS 值，如 "12%" / "2rem"）。 */
  top?: string;
  left?: string;
  right?: string;
  bottom?: string;
  /** 直径 px，默认 4。 */
  size?: number;
  /** flicker 起始相位错开（秒），默认 0。 */
  delay?: number;
}

interface FoxfireProps {
  /** 火花列表，建议 1–3 颗（DESIGN.md §3.4 每屏 motif 纪律）。 */
  sparks: Spark[];
  className?: string;
}

/**
 * 狐火 —— 幽蓝绿的「灵感闪现」点缀，神秘 accent 专属（绝不进数据面）。
 * 用 bg-foxfire 圆点 + .foxfire-flicker（reduced-motion 下静态）。
 * 父容器需 relative；火花 absolute 定位，pointer-events-none。
 */
export function Foxfire({ sparks, className }: FoxfireProps) {
  return (
    <div className={cn("pointer-events-none absolute inset-0 z-0", className)} aria-hidden="true">
      {sparks.map((s, i) => {
        const d = s.size ?? 4;
        return (
          <span
            key={i}
            className="foxfire-flicker absolute rounded-full bg-foxfire"
            style={{
              top: s.top,
              left: s.left,
              right: s.right,
              bottom: s.bottom,
              width: d,
              height: d,
              animationDelay: s.delay ? `${s.delay}s` : undefined,
            }}
          />
        );
      })}
    </div>
  );
}
