"use client";

import { cn } from "@/lib/cn";

interface TickerStripProps {
  items: string[];
  /** Optional join glyph between items. Default ◆ (diamond). */
  separator?: string;
  className?: string;
}

/**
 * Top-of-page scrolling marquee — see DESIGN.md §1 (visual anchor #5).
 * Doubles the line for a seamless loop; mono uppercase, hairline-bordered.
 */
export function TickerStrip({
  items,
  separator = "◆",
  className,
}: TickerStripProps) {
  const Line = ({ hidden = false }: { hidden?: boolean }) => (
    <span className="flex shrink-0 items-center" aria-hidden={hidden || undefined}>
      {items.map((it, i) => (
        <span key={i} className="flex items-center">
          {i > 0 ? (
            <span className="mx-5 text-seal/70" aria-hidden>
              {separator}
            </span>
          ) : null}
          <span>{it}</span>
        </span>
      ))}
    </span>
  );
  return (
    <div
      className={cn(
        "relative overflow-hidden border-y border-fg/15 bg-bg-deep/60",
        className,
      )}
    >
      <div className="flex whitespace-nowrap font-mono text-[11px] uppercase tracking-[0.26em] text-fg-muted">
        <div className="ticker-scroll flex shrink-0 gap-12 px-6 py-2.5">
          <Line />
          <Line hidden />
        </div>
      </div>
      <div
        aria-hidden
        className="pointer-events-none absolute inset-y-0 left-0 w-24 bg-gradient-to-r from-bg to-transparent"
      />
      <div
        aria-hidden
        className="pointer-events-none absolute inset-y-0 right-0 w-24 bg-gradient-to-l from-bg to-transparent"
      />
    </div>
  );
}
