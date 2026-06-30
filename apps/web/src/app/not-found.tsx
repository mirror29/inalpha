import Link from "next/link";

/**
 * Branded 404 page — bilingual, static, no i18n dependency.
 *
 * Runs outside [locale] layout in the static export, so next-intl hooks
 * are unavailable. Inline text covers both en/zh. Matches the Technical
 * Broadsheet aesthetic: Fraunces italic display, hairline rules, mono
 * labels, grain texture.
 */
export default function NotFoundPage() {
  return (
    <div className="relative flex min-h-screen items-center justify-center grain bg-bg text-fg">
      {/* Oversized italic "404" bleeding off the top-right — matches
          BroadsheetSection index numerals */}
      <span
        aria-hidden
        className="pointer-events-none absolute -top-16 -right-2 -z-10 select-none font-display italic leading-none text-fg/[0.04]"
        style={{ fontSize: "clamp(8rem, 24vw, 22rem)" }}
      >
        404
      </span>

      <main className="relative z-10 mx-auto max-w-2xl px-6 py-24 text-center">
        {/* Hairline bracket header */}
        <div className="border-y border-fg/15">
          <div className="flex items-center justify-center gap-6 py-3 font-mono text-[11px] uppercase tracking-[0.22em] text-fg-muted">
            <span className="flex items-center gap-2.5">
              <span
                className="inline-block h-3 w-[2px] bg-seal/70"
                aria-hidden="true"
              />
              <span>404 · not on the ledger</span>
            </span>
          </div>
        </div>

        {/* Display title */}
        <h1
          className="display-italic mt-12 text-fg leading-[1.02]"
          style={{
            fontSize: "clamp(2rem, 4.2vw, 3.25rem)",
            fontWeight: 400,
          }}
        >
          This page is not on the ledger.
        </h1>

        <p className="mt-4 max-w-[48ch] mx-auto text-[15px] leading-relaxed text-fg-muted">
          Queried a page that doesn&rsquo;t exist.
        </p>

        {/* ZH line — lighter, smaller, as a secondary annotation */}
        <p className="mt-3 font-sans text-[14px] text-fg-muted/60">
          此页不在账本上。查询了不存在的页面。
        </p>

        {/* Return link — matching the ghost button / CTAFooter link style */}
        <div className="mt-12">
          <Link
            href="/"
            className="inline-flex items-center gap-2 border border-fg/20 px-5 py-2.5 font-mono text-[12px] uppercase tracking-[0.22em] text-fg transition-colors hover:border-cyan hover:text-cyan"
          >
            Return to index
          </Link>
        </div>

        {/* Breadcrumb hint */}
        <p className="mt-8 font-mono text-[10px] uppercase tracking-[0.26em] text-fg-muted/40">
          inalpha.dev &mdash; an oracle that keeps a ledger
        </p>
      </main>
    </div>
  );
}
