import * as React from "react";

import { cn } from "@/lib/cn";

interface EmblemProps {
  /** 像素边长（正方形）。默认 40。 */
  size?: number;
  className?: string;
  /** 是否给纯装饰（aria-hidden）。默认 false（带 label，作品牌标记）。 */
  decorative?: boolean;
}

/**
 * Inalpha 朱印徽章 —— 矢量内联自 `docs/miro/brand/assets/09-logo-emblem-circle.svg`。
 * 双圆环 + 鸟居 + 狐头（金瞳 / α 瞳）+ 两侧勾玉 + 底部交叉稻穗。
 * 朱红 / 稻穗金 / 月白 / 墨四色，矢量任意缩放；朱红描线在暗/亮主题都可读。
 * 配 `.seal-glow` 作 hero 朱印落款，或 `.seal-stamp` 作首屏盖章 signature moment。
 */
export function Emblem({ size = 40, className, decorative = false }: EmblemProps) {
  return (
    <svg
      viewBox="0 0 256 256"
      width={size}
      height={size}
      className={cn("shrink-0", className)}
      role={decorative ? undefined : "img"}
      aria-label={decorative ? undefined : "Inalpha"}
      aria-hidden={decorative || undefined}
    >
      {!decorative && <title>Inalpha</title>}

      {/* 外圆环 */}
      <circle cx="128" cy="128" r="118" fill="none" stroke="#C8463C" strokeWidth="6" />
      <circle cx="128" cy="128" r="108" fill="none" stroke="#C8463C" strokeWidth="1.5" opacity="0.6" />

      {/* 顶部鸟居 */}
      <g transform="translate(128 28)" fill="#C8463C">
        <path d="M -34 0 Q 0 -9 34 0 L 30 4 L -30 4 Z" />
        <rect x="-30" y="5" width="60" height="3.5" />
        <rect x="-26" y="15" width="52" height="2.5" />
        <rect x="-22" y="9" width="5" height="20" />
        <rect x="17" y="9" width="5" height="20" />
      </g>

      {/* 中心狐头 */}
      <g transform="translate(128 142)">
        <path d="M -52 -28 L -40 -58 L -22 -22 Z" fill="#F5F0E8" stroke="#C8463C" strokeWidth="2" />
        <path d="M -46 -32 L -40 -52 L -30 -32 Z" fill="#C8463C" />
        <path d="M 52 -28 L 40 -58 L 22 -22 Z" fill="#F5F0E8" stroke="#C8463C" strokeWidth="2" />
        <path d="M 46 -32 L 40 -52 L 30 -32 Z" fill="#C8463C" />
        <path
          d="M -48 -10 Q -50 22 -18 38 Q 0 44 18 38 Q 50 22 48 -10 Q 44 -28 0 -34 Q -44 -28 -48 -10 Z"
          fill="#F5F0E8"
          stroke="#C8463C"
          strokeWidth="2"
        />
        <path d="M 0 -22 L -5 -15 L 0 -12 L 5 -15 Z" fill="#C8463C" opacity="0.7" />
        <ellipse cx="0" cy="10" rx="3" ry="2" fill="#1A1714" />
        <path d="M -10 18 Q 0 25 10 18" fill="none" stroke="#1A1714" strokeWidth="2" strokeLinecap="round" />
        <path d="M 0 12 L 0 19" stroke="#1A1714" strokeWidth="1.5" strokeLinecap="round" />
        <ellipse cx="-22" cy="-4" rx="7" ry="5" fill="#F5F0E8" stroke="#1A1714" strokeWidth="1.5" />
        <ellipse cx="-22" cy="-4" rx="3" ry="4" fill="#D4A744" />
        <circle cx="-22" cy="-4" r="1.5" fill="#1A1714" />
        <ellipse cx="22" cy="-4" rx="7" ry="5" fill="#F5F0E8" stroke="#1A1714" strokeWidth="1.5" />
        <text
          x="22"
          y="2"
          textAnchor="middle"
          fontFamily="Georgia, 'Times New Roman', serif"
          fontSize="11"
          fontWeight="bold"
          fontStyle="italic"
          fill="#D4A744"
        >
          α
        </text>
        <path d="M -36 12 L -52 14" stroke="#C8463C" strokeWidth="1.2" strokeLinecap="round" />
        <path d="M -36 16 L -54 22" stroke="#C8463C" strokeWidth="1.2" strokeLinecap="round" />
        <path d="M 36 12 L 52 14" stroke="#C8463C" strokeWidth="1.2" strokeLinecap="round" />
        <path d="M 36 16 L 54 22" stroke="#C8463C" strokeWidth="1.2" strokeLinecap="round" />
      </g>

      {/* 两侧勾玉 */}
      <g transform="translate(28 128) rotate(-30)">
        <path
          d="M 0 -10 Q 12 -10 12 2 Q 12 14 -2 14 Q -10 14 -10 6 Q -10 -2 -2 -2 Q 4 -2 4 4"
          stroke="#C8463C"
          strokeWidth="0.8"
          fill="#D4A744"
        />
        <circle cx="-4" cy="6" r="2" fill="#C8463C" />
      </g>
      <g transform="translate(228 128) rotate(150)">
        <path
          d="M 0 -10 Q 12 -10 12 2 Q 12 14 -2 14 Q -10 14 -10 6 Q -10 -2 -2 -2 Q 4 -2 4 4"
          stroke="#C8463C"
          strokeWidth="0.8"
          fill="#D4A744"
        />
        <circle cx="-4" cy="6" r="2" fill="#C8463C" />
      </g>

      {/* 底部交叉稻穗 */}
      <g transform="translate(128 220)" fill="#D4A744" stroke="#D4A744" strokeWidth="1">
        <g>
          <path d="M -25 5 Q -32 -8 -42 -28" strokeWidth="1.5" fill="none" />
          <ellipse cx="-28" cy="-2" rx="2.5" ry="1.8" transform="rotate(-15 -28 -2)" />
          <ellipse cx="-32" cy="-8" rx="2.5" ry="1.8" transform="rotate(-25 -32 -8)" />
          <ellipse cx="-36" cy="-15" rx="2.5" ry="1.8" transform="rotate(-35 -36 -15)" />
          <ellipse cx="-39" cy="-22" rx="2.5" ry="1.8" transform="rotate(-40 -39 -22)" />
          <ellipse cx="-41" cy="-28" rx="2.5" ry="1.8" transform="rotate(-50 -41 -28)" />
        </g>
        <g>
          <path d="M 25 5 Q 32 -8 42 -28" strokeWidth="1.5" fill="none" />
          <ellipse cx="28" cy="-2" rx="2.5" ry="1.8" transform="rotate(15 28 -2)" />
          <ellipse cx="32" cy="-8" rx="2.5" ry="1.8" transform="rotate(25 32 -8)" />
          <ellipse cx="36" cy="-15" rx="2.5" ry="1.8" transform="rotate(35 36 -15)" />
          <ellipse cx="39" cy="-22" rx="2.5" ry="1.8" transform="rotate(40 39 -22)" />
          <ellipse cx="41" cy="-28" rx="2.5" ry="1.8" transform="rotate(50 41 -28)" />
        </g>
        <circle cx="0" cy="3" r="3" fill="#C8463C" />
      </g>
    </svg>
  );
}
