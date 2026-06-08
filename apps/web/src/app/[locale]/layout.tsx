import type { Metadata } from "next";
import { NextIntlClientProvider, hasLocale } from "next-intl";
import { getMessages, getTranslations, setRequestLocale } from "next-intl/server";
import { notFound } from "next/navigation";

import { routing } from "@/i18n/routing";

export async function generateStaticParams() {
  return routing.locales.map((locale) => ({ locale }));
}

const JSON_LD = {
  en: [
    {
      "@context": "https://schema.org",
      "@type": "Organization",
      "name": "Inalpha",
      "url": "https://inalpha.dev",
      "description":
        "Open-source quant agent framework — factor lab, risk engine, strategy evolution, plan-exec audit trail. Engineering discipline from Claude Code, unified kernel from NautilusTrader.",
      "sameAs": [
        "https://github.com/mirror29/inalpha",
        "https://inalpha.substack.com",
      ],
    },
    {
      "@context": "https://schema.org",
      "@type": "WebSite",
      "name": "Inalpha",
      "url": "https://inalpha.dev",
      "description":
        "Open-source quant agent framework — factor lab, risk engine, strategy evolution, plan-exec audit trail.",
      "potentialAction": {
        "@type": "SearchAction",
        "target": {
          "@type": "EntryPoint",
          "urlTemplate": "https://inalpha.dev/?q={search_term_string}",
        },
        "query-input": "required name=search_term_string",
      },
    },
    {
      "@context": "https://schema.org",
      "@type": "SoftwareSourceCode",
      name: "Inalpha",
      description:
        "Open-source quant agent framework: factor lab, risk engine, strategy evolution, audit-grade plan/exec. Engineering discipline borrowed from Claude Code.",
      codeRepository: "https://github.com/mirror29/inalpha",
      programmingLanguage: ["TypeScript", "Python"],
      license: "https://www.gnu.org/licenses/agpl-3.0.en.html",
      applicationCategory: "Finance",
      keywords:
        "quantitative trading, agent framework, LLM orchestration, factor research, backtesting, algorithmic trading, hooks, permissions, plan-exec, audit trail, Claude Code, NautilusTrader",
      author: { "@type": "Person", "name": "Miro" },
      about: {
        "@type": "Thing",
        name: "Audit-grade LLM agent infrastructure for quantitative finance",
        description:
          "Hooks, scoped permissions, plan-exec approval tokens, and signed audit trails — Claude Code engineering patterns applied to trading, with NautilusTrader's unified backtest=paper=live kernel.",
      },
    },
    {
      "@context": "https://schema.org",
      "@type": "FAQPage",
      mainEntity: [
        {
          "@type": "Question",
          name: "What is Inalpha?",
          acceptedAnswer: {
            "@type": "Answer",
            text: "Inalpha is an open-source quant agent framework that applies engineering discipline to LLM-driven trading. It treats AI agents not as black-box signal generators, but as code-writing collaborators bounded by hooks, permissions, plan-exec approval, and signed audit trails.",
          },
        },
        {
          "@type": "Question",
          name: "How is Inalpha different from NautilusTrader or vnpy?",
          acceptedAnswer: {
            "@type": "Answer",
            text: "Inalpha inherits the event-driven kernel from NautilusTrader and multi-market gateway philosophy from vnpy, then adds an audit-grade middleware layer: hooks, scoped permissions, plan-exec approval tokens, and signed audit trails inspired by Claude Code. Traditional quant frameworks focus on execution speed; Inalpha focuses on making every decision provable and replayable.",
          },
        },
        {
          "@type": "Question",
          name: "Can I trade real money with Inalpha?",
          acceptedAnswer: {
            "@type": "Answer",
            text: "No, and that's deliberate. As of Phase D-11 Inalpha ships an autonomous paper runner — promoted strategies trade a simulated account on live market data, machine-approved through plan/exec with a full decision-replay log. But orders are matched locally; there is no live brokerage integration and real-capital trading is out of the current plan.",
          },
        },
        {
          "@type": "Question",
          name: "What markets does Inalpha cover?",
          acceptedAnswer: {
            "@type": "Answer",
            text: "Crypto via CCXT, US equities, A-shares, Hong Kong, Japan, Korea, Australia, India, UK, Germany, global indices, and FRED macro data — all through one orchestrator and one codebase.",
          },
        },
        {
          "@type": "Question",
          name: "Is Inalpha free?",
          acceptedAnswer: {
            "@type": "Answer",
            text: "Inalpha is AGPL-3.0 licensed: free for personal, academic, and commercial in-house use. Network service providers must release modifications. Dual licensing available for proprietary use.",
          },
        },
      ],
    },
  ],
  zh: [
    {
      "@context": "https://schema.org",
      "@type": "Organization",
      "name": "Inalpha",
      "url": "https://inalpha.dev",
      "description":
        "开源量化 agent 框架——因子实验室、风控引擎、策略进化、审计级 plan/exec。工程纪律借鉴 Claude Code，统一内核继承 NautilusTrader。",
      "sameAs": [
        "https://github.com/mirror29/inalpha",
        "https://inalpha.substack.com",
      ],
    },
    {
      "@context": "https://schema.org",
      "@type": "WebSite",
      "name": "Inalpha",
      "url": "https://inalpha.dev",
      "description":
        "开源量化 agent 框架——因子实验室、风控引擎、策略进化、审计级 plan/exec。",
      "potentialAction": {
        "@type": "SearchAction",
        "target": {
          "@type": "EntryPoint",
          "urlTemplate": "https://inalpha.dev/?q={search_term_string}",
        },
        "query-input": "required name=search_term_string",
      },
    },
    {
      "@context": "https://schema.org",
      "@type": "SoftwareSourceCode",
      name: "Inalpha",
      description:
        "开源专业量化 agent 框架：因子实验室、风控引擎、策略进化、审计级 plan/exec。借鉴 Claude Code 的 hooks/permissions 模式和 NautilusTrader 的 backtest=paper=live 内核。",
      codeRepository: "https://github.com/mirror29/inalpha",
      programmingLanguage: ["TypeScript", "Python"],
      license: "https://www.gnu.org/licenses/agpl-3.0.en.html",
      applicationCategory: "Finance",
      keywords:
        "量化交易, agent 框架, LLM 编排, 因子研究, 回测, 算法交易, hooks, permissions, plan-exec, 审计链路, Claude Code, NautilusTrader",
      author: { "@type": "Person", "name": "Miro" },
      about: {
        "@type": "Thing",
        name: "面向专业量化的审计级 LLM agent 基础设施",
        description:
          "借鉴 Claude Code 的 hooks、scoped permissions、plan-exec 审批 token 和签名审计链，叠加 NautilusTrader 的 backtest=paper=live 统一内核，构建可审计的量化 agent 框架。",
      },
    },
    {
      "@context": "https://schema.org",
      "@type": "FAQPage",
      mainEntity: [
        {
          "@type": "Question",
          name: "Inalpha 是什么？",
          acceptedAnswer: {
            "@type": "Answer",
            text: "Inalpha 是一个开源专业量化 agent 框架，将工程纪律引入 LLM 驱动交易。将 agent 视为受 hooks、permissions、plan-exec 审批和签名审计链约束的代码协作者——LLM 写策略代码，工程 harness 为每个决策签名并执行每条护栏。",
          },
        },
        {
          "@type": "Question",
          name: "Inalpha 和 NautilusTrader、vnpy 这些量化框架有什么不同？",
          acceptedAnswer: {
            "@type": "Answer",
            text: "Inalpha 继承了 NautilusTrader 的事件驱动内核设计和 vnpy 的多市场 Gateway 抽象哲学，额外叠加审计级中间件——hooks、scoped permissions、plan-exec 一次性审批 token、签名审计链——借鉴 Claude Code 的工程模式。传统框架关注执行速度，Inalpha 关注让每个决策都可证明、可回放。",
          },
        },
        {
          "@type": "Question",
          name: "现在能用 Inalpha 交易真金白银吗？",
          acceptedAnswer: {
            "@type": "Answer",
            text: "不能，这是当前的有意设计。到 Phase D-11 为止 Inalpha 已带无人值守模拟盘 runner——promoted 策略在真实行情上自动跑模拟账户，经 plan/exec 机器审批、留完整决策复盘日志。但订单一律本地撮合，没有接入实盘券商，真金交易不在当前计划内。",
          },
        },
        {
          "@type": "Question",
          name: "Inalpha 覆盖哪些市场？",
          acceptedAnswer: {
            "@type": "Answer",
            text: "加密货币（CEX 通过 CCXT）、美股、A 股、港股、日股、韩股、澳股、印度、英股、德股、全球指数和 FRED 宏观数据——全部通过同一个 orchestrator 和同一套代码库。",
          },
        },
        {
          "@type": "Question",
          name: "Inalpha 免费吗？",
          acceptedAnswer: {
            "@type": "Answer",
            text: "Inalpha 采用 AGPL-3.0 许可证：个人研究、学术用途和商业内部使用均免费。以网络服务形式提供时必须公开修改。专有或闭源商业使用可提 issue 讨论双重许可。",
          },
        },
      ],
    },
  ],
};

export async function generateMetadata({
  params,
}: {
  params: Promise<{ locale: string }>;
}): Promise<Metadata> {
  const { locale } = await params;
  const t = await getTranslations({ locale, namespace: "meta" });

  const alternates = {
    canonical: `https://inalpha.dev${locale === routing.defaultLocale ? "" : `/${locale}`}/`,
    languages: {
      en: "https://inalpha.dev/",
      zh: "https://inalpha.dev/zh/",
      "x-default": "https://inalpha.dev/",
    },
  };

  return {
    title: t("title"),
    description: t("description"),
    metadataBase: new URL("https://inalpha.dev"),
    alternates,
    openGraph: {
      title: t("title"),
      description: t("description"),
      url: `https://inalpha.dev${locale === routing.defaultLocale ? "" : `/${locale}`}/`,
      siteName: "Inalpha",
      images: [
        {
          url: "/og.png",
          width: 1200,
          height: 630,
          alt: t("title"),
        },
      ],
      type: "website",
    },
    twitter: {
      card: "summary_large_image",
      title: t("title"),
      description: t("description"),
      images: ["/og.png"],
    },
    robots: {
      index: true,
      follow: true,
      "max-snippet": -1,
      "max-image-preview": "large",
    },
  };
}

/**
 * Locale layout — just wires next-intl. `<html>` / `<body>` and font
 * variables live in the root layout (`src/app/layout.tsx`).
 */
export default async function LocaleLayout({
  children,
  params,
}: {
  children: React.ReactNode;
  params: Promise<{ locale: string }>;
}) {
  const { locale } = await params;
  if (!hasLocale(routing.locales, locale)) {
    notFound();
  }
  setRequestLocale(locale);
  const messages = await getMessages();

  const jsonLd =
    JSON_LD[locale as keyof typeof JSON_LD] ?? JSON_LD[routing.defaultLocale];

  return (
    <NextIntlClientProvider messages={messages}>
      {jsonLd.map((schema, i) => (
        <script
          key={i}
          type="application/ld+json"
          dangerouslySetInnerHTML={{ __html: JSON.stringify(schema) }}
        />
      ))}
      {children}
    </NextIntlClientProvider>
  );
}
