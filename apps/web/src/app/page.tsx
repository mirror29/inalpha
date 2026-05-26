/**
 * Root entry — there is no locale-less homepage. Static export can't
 * server-redirect, so we ship a minimal HTML stub: a synchronous inline
 * script that fires before the browser paints any body content, plus a
 * <noscript> meta-refresh fallback. In production behind Cloudflare
 * Pages, `public/_redirects` short-circuits before this page is served.
 */
export default function RootPage() {
  return (
    <>
      <script
        dangerouslySetInnerHTML={{
          __html: 'window.location.replace("/en/")',
        }}
      />
      <noscript>
        <meta httpEquiv="refresh" content="0; url=/en/" />
      </noscript>
    </>
  );
}
