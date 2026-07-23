import Link from "next/link";

import { DocumentShell } from "./_shared/DocumentShell";

export default function GlobalNotFound() {
  return (
    <DocumentShell lang="en">
      <div className="relative flex min-h-screen items-center justify-center grain bg-bg text-fg">
        <span
          aria-hidden
          className="pointer-events-none absolute -top-16 -right-2 -z-10 select-none font-display italic leading-none text-fg/[0.04]"
          style={{ fontSize: "clamp(8rem, 24vw, 22rem)" }}
        >
          404
        </span>
        <main className="relative z-10 mx-auto max-w-2xl px-6 py-24 text-center">
          <div className="border-y border-fg/15 py-3 font-mono text-[11px] uppercase tracking-[0.22em] text-fg-muted">
            404 · not on the ledger
          </div>
          <h1
            className="display-italic mt-12 text-fg leading-[1.02]"
            style={{ fontSize: "clamp(2rem, 4.2vw, 3.25rem)", fontWeight: 400 }}
          >
            This page is not on the ledger.
          </h1>
          <p className="mt-4 text-[15px] leading-relaxed text-fg-muted">
            Queried a page that doesn&rsquo;t exist.
          </p>
          <p className="mt-3 font-sans text-[14px] text-fg-muted/60">
            此页不在账本上。查询了不存在的页面。
          </p>
          <div className="mt-12">
            <Link
              href="/"
              className="inline-flex items-center gap-2 border border-fg/20 px-5 py-2.5 font-mono text-[12px] uppercase tracking-[0.22em] text-fg transition-colors hover:border-cyan hover:text-cyan"
            >
              Return to index
            </Link>
          </div>
        </main>
      </div>
    </DocumentShell>
  );
}
