import { cn } from "@/lib/cn";

interface CodeDiffProps {
  before: string[];
  after: string[];
  beforeLabel?: string;
  afterLabel?: string;
  /** Reserved for future language hinting — currently unused. */
  language?: "python" | "ts" | "yaml";
  className?: string;
}

/**
 * Tiny inline syntax tinting. Intentionally regex-only, no shiki/prismjs
 * (the goal is hero-section visual cue, not a fully featured highlighter).
 * If you need rich highlighting, render Markdown server-side instead.
 */
function tint(line: string): React.ReactNode {
  const parts: React.ReactNode[] = [];
  const tokenRe =
    /(#[^\n]*$|"[^"]*"|'[^']*'|\b(?:from|import|def|class|return|if|else|for|in|while|with|as|yield|lambda|None|True|False)\b|\b\d+(?:\.\d+)?\b)/g;
  let last = 0;
  let m: RegExpExecArray | null;
  let key = 0;
  while ((m = tokenRe.exec(line))) {
    if (m.index > last) parts.push(line.slice(last, m.index));
    const tok = m[0];
    let cls = "";
    if (tok.startsWith("#")) cls = "text-fg-muted/70 italic";
    else if (tok.startsWith('"') || tok.startsWith("'")) cls = "text-gold";
    else if (/^\d/.test(tok)) cls = "text-bull";
    else cls = "text-cyan";
    parts.push(
      <span key={key++} className={cls}>
        {tok}
      </span>,
    );
    last = m.index + tok.length;
  }
  if (last < line.length) parts.push(line.slice(last));
  return parts;
}

/**
 * Two-column before / after code panel. Lines that differ at the same index
 * get visually marked — `-` left red, `+` right green. Identical lines stay
 * neutral. See DESIGN.md §7.2 — drives the "backtest → live" demo.
 */
export function CodeDiff({
  before,
  after,
  beforeLabel = "before",
  afterLabel = "after",
  className,
}: CodeDiffProps) {
  const rows = Math.max(before.length, after.length);
  return (
    <div
      className={cn(
        "grid gap-px overflow-hidden rounded-lg border border-border-subtle bg-border-subtle md:grid-cols-2",
        className,
      )}
    >
      <DiffColumn lines={before} sibling={after} label={beforeLabel} kind="before" />
      <DiffColumn lines={after} sibling={before} label={afterLabel} kind="after" />
      {/* rows is used only to compute padding parity in mobile stacked layout */}
      <span hidden aria-hidden data-rows={rows} />
    </div>
  );
}

function DiffColumn({
  lines,
  sibling,
  label,
  kind,
}: {
  lines: string[];
  sibling: string[];
  label: string;
  kind: "before" | "after";
}) {
  const accent = kind === "before" ? "text-fox-red" : "text-bull";
  const marker = kind === "before" ? "-" : "+";
  return (
    <div className="bg-bg-deep font-mono text-[13px]">
      <header className="flex items-center gap-2 border-b border-border-subtle px-4 py-2 text-[11px] uppercase tracking-[0.18em] text-fg-muted">
        <span className={accent}>{marker}</span>
        {label}
      </header>
      <pre className="overflow-x-auto px-4 py-3 leading-relaxed">
        <code>
          {lines.map((line, i) => {
            const changed = line !== sibling[i];
            return (
              <div
                key={i}
                className={cn(
                  "px-2 py-0.5",
                  changed && kind === "before" && "border-l-2 border-fox-red bg-fox-red/10",
                  changed && kind === "after" && "border-l-2 border-bull bg-bull/10",
                )}
              >
                {tint(line)}
              </div>
            );
          })}
        </code>
      </pre>
    </div>
  );
}
