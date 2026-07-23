/**
 * Privacy Policy — bilingual standalone page (no i18n dependency).
 *
 * Uses the Technical Broadsheet editorial chrome: Fraunces display title,
 * hairline rules, mono labels, grain texture. Inline en/zh content.
 * Same pattern as the 404 not-found page — works everywhere, no locale routing.
 */
import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Privacy Policy | Inalpha",
  description: "How the Inalpha website handles privacy, storage, and third parties.",
  alternates: { canonical: "https://inalpha.dev/privacy/" },
};

export default function PrivacyPage() {
  return (
    <div className="relative min-h-screen grain bg-bg text-fg">
      {/* Top bar */}
      <nav className="border-b border-fg/15">
        <div className="mx-auto flex max-w-[96rem] items-center justify-between gap-6 px-6 py-3 font-mono text-[11px] uppercase tracking-[0.22em] text-fg-muted md:px-14">
          <span className="flex items-center gap-2.5">
            <span
              className="inline-block h-3 w-[2px] bg-seal/70"
              aria-hidden="true"
            />
            <a href="/" className="transition-colors hover:text-cyan">
              Inalpha
            </a>
            <span className="text-fg/30">/</span>
            <span>Privacy</span>
          </span>
          <a
            href="/"
            className="text-fg-muted/50 transition-colors hover:text-fg"
          >
            ← Back&nbsp;/&nbsp;返回
          </a>
        </div>
      </nav>

      <main className="mx-auto max-w-[96rem] px-6 py-16 md:px-14 md:py-24">
        {/* Oversized watermark "P" */}
        <span
          aria-hidden
          className="pointer-events-none absolute -top-16 right-4 -z-10 select-none font-display italic leading-none text-fg/[0.04] md:right-12"
          style={{ fontSize: "clamp(8rem, 24vw, 22rem)" }}
        >
          P
        </span>

        <hgroup className="mb-16">
          <h1
            className="display-italic text-fg leading-[1.02]"
            style={{
              fontSize: "clamp(2rem, 4.2vw, 3.25rem)",
              fontWeight: 400,
            }}
          >
            What we know about you.{" "}
            <span className="text-fg-muted/60">(Nothing.)</span>
          </h1>
          <p className="mt-3 font-sans text-[14px] text-fg-muted/60">
            关于你，我们知道什么。
            <span className="text-fg-muted/40">（什么都不知道。）</span>
          </p>
          <p className="mt-4 max-w-[62ch] text-[15px] leading-relaxed text-fg-muted">
            Inalpha is a static site. No cookies, no analytics, no tracking of
            any kind. Here is the full inventory.
          </p>
        </hgroup>

        <div className="space-y-12 border-t border-fg/12 pt-12">
          <Block
            enLabel="Hosting"
            zhLabel="托管"
            enBody="Inalpha is served from Cloudflare Pages. Cloudflare may log access at the CDN level (IP, user-agent, timestamp) as part of normal operations. We do not access, collect, or store these logs."
            zhBody="Inalpha 通过 Cloudflare Pages 提供服务。Cloudflare 可能在 CDN 层面记录访问信息（IP、user-agent、时间戳），属于正常运维。我们不访问、不收集、不存储这些日志。"
          />
          <Block
            enLabel="Local storage"
            zhLabel="本地存储"
            enBody="Your theme preference (dark or light) is saved in your browser's localStorage. It never leaves your device. We never read it on the server — there is no server."
            zhBody="你的主题偏好（深色/浅色）保存在浏览器的 localStorage 中，永远不离开你的设备。我们不会在服务端读取——根本没有服务端。"
          />
          <Block
            enLabel="Cookies"
            zhLabel="Cookie"
            enBody="None. Zero. Inalpha sets no cookies whatsoever."
            zhBody="没有。零。Inalpha 不设置任何 cookie。"
          />
          <Block
            enLabel="Third parties"
            zhLabel="第三方"
            enBody="None. No analytics scripts, no tracking pixels, no embedded content from third-party domains."
            zhBody="没有。无统计分析脚本，无追踪像素，无第三方域名嵌入内容。"
          />
          <Block
            enLabel="External links"
            zhLabel="外部链接"
            enBody="This site links to GitHub and Substack. Those services have their own privacy practices and are not covered by this policy."
            zhBody="本站链接到 GitHub 和 Substack。这些服务有各自的隐私政策，不在本声明范围内。"
          />
          <Block
            enLabel="Contact"
            zhLabel="联系"
            enBody="For privacy questions, open an issue on GitHub or email the maintainer listed in the repository."
            zhBody="如有隐私相关问题，请在 GitHub 提 issue 或联系仓库中列出的维护者。"
          />
        </div>

        <div className="mt-16 border-t border-fg/12 pt-8 font-mono text-[11px] uppercase tracking-[0.22em] text-fg-muted/60">
          <p>Effective: 2026-06-30&ensp;/&ensp;生效日期：2026-06-30</p>
          <a
            href="/"
            className="mt-3 inline-block text-cyan transition-colors hover:text-fg"
          >
            ← Back to index&ensp;/&ensp;返回首页
          </a>
        </div>
      </main>
    </div>
  );
}

function Block({
  enLabel,
  zhLabel,
  enBody,
  zhBody,
}: {
  enLabel: string;
  zhLabel: string;
  enBody: string;
  zhBody: string;
}) {
  return (
    <div>
      <h2 className="font-mono text-[11px] uppercase tracking-[0.22em] text-fg/50">
        {enLabel}
        <span className="text-fg/30">&ensp;·&ensp;</span>
        <span className="normal-case">{zhLabel}</span>
      </h2>
      <p className="mt-2 max-w-[68ch] text-[15px] leading-relaxed text-fg-muted">
        {enBody}
      </p>
      <p className="mt-1 font-sans text-[14px] leading-relaxed text-fg-muted/60">
        {zhBody}
      </p>
    </div>
  );
}
