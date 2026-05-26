"use client";

import { useEffect, useRef, useState } from "react";
import { useReducedMotion } from "motion/react";

import { cn } from "@/lib/cn";
import { TYPEWRITER_MS_PER_CHAR } from "@/lib/motion";

interface TerminalBlockProps {
  /** Shell prompt prefix. Defaults to `$ inalpha>`. */
  prompt?: string;
  /**
   * Lines to render. Pass a string for one-liners, array for multi-line.
   * Typewriter walks character-by-character across the entire flattened text.
   */
  content: string | string[];
  /** Replay-able typewriter animation. Off by default. */
  typewriter?: boolean;
  /** Pin to the viewport while the parent section scrolls past. */
  scrollPin?: boolean;
  /** Optional caption above the chrome (e.g., file name). */
  caption?: string;
  className?: string;
}

/**
 * macOS-chrome terminal block. Used by EngineeringHarness §10.6 and
 * AgentDebateDemo §10.4. No syntax highlighting — pure mono.
 *
 * Typewriter respects `prefers-reduced-motion`: full text is shown immediately.
 */
export function TerminalBlock({
  prompt = "$ inalpha>",
  content,
  typewriter = false,
  scrollPin = false,
  caption,
  className,
}: TerminalBlockProps) {
  const lines = Array.isArray(content) ? content : [content];
  const fullText = lines.join("\n");
  const reduced = useReducedMotion();
  const [shown, setShown] = useState(typewriter && !reduced ? "" : fullText);
  const ranRef = useRef(false);

  useEffect(() => {
    if (!typewriter || reduced || ranRef.current) return;
    ranRef.current = true;
    let i = 0;
    const id = window.setInterval(() => {
      i += 1;
      setShown(fullText.slice(0, i));
      if (i >= fullText.length) window.clearInterval(id);
    }, TYPEWRITER_MS_PER_CHAR);
    return () => window.clearInterval(id);
  }, [typewriter, reduced, fullText]);

  return (
    <div
      className={cn(
        "overflow-hidden rounded-lg border border-border-subtle bg-bg-deep font-mono text-sm",
        scrollPin && "sticky top-24",
        className,
      )}
    >
      {/* macOS chrome — three dots use brand tri-color, not the OS palette */}
      <div className="flex items-center gap-2 border-b border-border-subtle px-4 py-2.5">
        <span aria-hidden className="size-2.5 rounded-full bg-fox-red/70" />
        <span aria-hidden className="size-2.5 rounded-full bg-gold/70" />
        <span aria-hidden className="size-2.5 rounded-full bg-bull/70" />
        {caption ? (
          <span className="ml-3 font-mono text-[11px] text-fg-muted">{caption}</span>
        ) : null}
      </div>
      <pre className="overflow-x-auto px-4 py-3 leading-relaxed text-fg-muted">
        <code>
          <span className="select-none text-cyan/70">{prompt}</span>{" "}
          <span className="text-fg">{shown}</span>
          {typewriter && !reduced && shown.length < fullText.length ? (
            <span aria-hidden className="ml-0.5 inline-block w-2 animate-pulse bg-cyan">
              &nbsp;
            </span>
          ) : null}
        </code>
      </pre>
    </div>
  );
}
