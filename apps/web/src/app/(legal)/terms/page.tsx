/**
 * Terms of Service — bilingual standalone page (no i18n dependency).
 *
 * Critical disclaimers for a finance-adjacent open-source project:
 * not financial advice, no real-money execution, AGPL-3.0, no warranty,
 * risk disclosure, IP separation.
 *
 * Uses the Technical Broadsheet editorial chrome. The disclaimer block
 * uses a fox-red left-border callout — matching the caution/risk visual
 * language from the main site.
 */
import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Terms of Service | Inalpha",
  description: "Terms, financial risk disclosure, and license information for Inalpha.",
  alternates: { canonical: "https://inalpha.dev/terms/" },
};

export default function TermsPage() {
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
            <span>Terms</span>
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
        <hgroup className="mb-16">
          <h1
            className="display-italic text-fg leading-[1.02]"
            style={{
              fontSize: "clamp(2rem, 4.2vw, 3.25rem)",
              fontWeight: 400,
            }}
          >
            What you are agreeing to.{" "}
            <span className="text-fg-muted/60">
              (And what you are not.)
            </span>
          </h1>
          <p className="mt-3 font-sans text-[14px] text-fg-muted/60">
            你同意什么。
            <span className="text-fg-muted/40">（以及不同意什么。）</span>
          </p>
          <p className="mt-4 max-w-[62ch] text-[15px] leading-relaxed text-fg-muted">
            By using Inalpha — the website, the code, or any output — you
            accept these terms. If you do not agree, do not use the software.
          </p>
        </hgroup>

        <div className="space-y-12 border-t border-fg/12 pt-12">
          {/* Disclaimer — fox-red caution callout */}
          <div className="border-l-2 border-seal/60 bg-bg-deep px-6 py-5">
            <h2 className="font-mono text-[11px] uppercase tracking-[0.22em] text-seal/80">
              Not financial advice&ensp;/&ensp;
              <span className="normal-case">非财务建议</span>
            </h2>
            <p className="mt-3 max-w-[68ch] text-[15px] leading-relaxed text-fg-muted">
              Inalpha is experimental research software in alpha stage. Nothing
              on this site, in the repository, or output by any agent
              constitutes financial, investment, legal, or tax advice. Past
              performance — real or simulated — does not guarantee future
              results. The framework does not execute real-capital trades. Do
              not route real money through Inalpha.
            </p>
            <p className="mt-2 font-sans text-[14px] leading-relaxed text-fg-muted/60">
              Inalpha 是处于 alpha 阶段的实验性研究软件。本网站、代码仓库或任何 agent
              输出的内容均不构成财务、投资、法律或税务建议。过往表现——无论真实或模拟——不保证未来结果。本框架不执行真实资金交易。请勿将真实资金接入 Inalpha。
            </p>
          </div>

          <Block
            enLabel="Risk disclosure"
            zhLabel="风险披露"
            enBody="Trading involves substantial risk of loss and is not suitable for everyone. Simulated trading results have inherent limitations — unlike actual trading, simulated results do not represent actual trading, may under-compensate for market impact, and lack real-market liquidity constraints. Strategies that perform well in backtests or paper may perform differently in live markets."
            zhBody="交易存在重大亏损风险，并非适合所有人。模拟交易结果具有固有局限性——与真实交易不同，模拟结果不代表实际交易，可能低估市场冲击，缺乏真实市场流动性约束。回测或模拟盘中表现良好的策略在真实市场中可能表现不同。"
          />

          <Block
            enLabel="License"
            zhLabel="许可证"
            enBody="Inalpha is licensed under AGPL-3.0. You may use, study, modify, and distribute it freely. If you offer Inalpha as a network service, you must release your modifications under the same license. Dual licensing is available for proprietary use — open an issue on GitHub to discuss."
            zhBody="Inalpha 基于 AGPL-3.0 许可。你可以自由使用、研究、修改和分发。如果你以网络服务形式提供 Inalpha，必须按相同许可公开你的修改。闭源或专有商业使用可提 issue 讨论双重许可。"
          />

          <Block
            enLabel="No warranty"
            zhLabel="无担保"
            enBody='The software is provided "as is," without warranty of any kind, express or implied. The authors and contributors are not liable for any damages arising from its use.'
            zhBody="本软件按「原样」提供，不附带任何明示或默示的担保。作者和贡献者不对因使用本软件产生的任何损害承担责任。"
          />

          <Block
            enLabel="Intellectual property"
            zhLabel="知识产权"
            enBody="The Inalpha name, logo, mascot (kitsune shrine maiden), and brand assets are property of the project. The code is AGPL-3.0; the brand is not."
            zhBody="Inalpha 名称、标志、吉祥物（狐娘）及品牌资产归项目所有。代码按 AGPL-3.0 许可；品牌不在此列。"
          />
        </div>

        <div className="mt-16 border-t border-fg/12 pt-8 font-mono text-[11px] uppercase tracking-[0.22em] text-fg-muted/60">
          <a
            href="/"
            className="text-cyan transition-colors hover:text-fg"
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
