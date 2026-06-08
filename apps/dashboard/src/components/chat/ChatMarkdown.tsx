"use client";

import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";

/**
 * agent 气泡的 markdown 渲染 —— 组件级覆盖样式,套用「印章终端」主题
 * (电光青链接 / 等宽 code / hairline 表格),不引 prose 插件保持轻量。
 */
const COMPONENTS: Components = {
  p: ({ children }) => <p className="my-1.5 first:mt-0 last:mb-0">{children}</p>,
  a: ({ children, href }) => (
    <a
      href={href}
      target="_blank"
      rel="noreferrer"
      className="text-cyan underline decoration-cyan/40 underline-offset-2 hover:decoration-cyan"
    >
      {children}
    </a>
  ),
  strong: ({ children }) => (
    <strong className="font-semibold text-fg">{children}</strong>
  ),
  em: ({ children }) => <em className="italic">{children}</em>,
  ul: ({ children }) => (
    <ul className="my-1.5 list-disc space-y-0.5 pl-4">{children}</ul>
  ),
  ol: ({ children }) => (
    <ol className="my-1.5 list-decimal space-y-0.5 pl-4">{children}</ol>
  ),
  li: ({ children }) => <li className="leading-relaxed">{children}</li>,
  h1: ({ children }) => (
    <h3 className="mb-1 mt-2 font-display text-base text-fg">{children}</h3>
  ),
  h2: ({ children }) => (
    <h3 className="mb-1 mt-2 font-display text-base text-fg">{children}</h3>
  ),
  h3: ({ children }) => (
    <h4 className="mb-1 mt-2 font-display text-sm text-fg">{children}</h4>
  ),
  blockquote: ({ children }) => (
    <blockquote className="my-1.5 border-l-2 border-seal/50 pl-3 text-fg-muted">
      {children}
    </blockquote>
  ),
  code: ({ className, children }) => {
    // 块级 code 带 language-* className;行内 code 没有。
    const isBlock = Boolean(className);
    if (isBlock) {
      return (
        <code className="block font-mono text-[11px] leading-relaxed">
          {children}
        </code>
      );
    }
    return (
      <code className="rounded bg-bg-deep/70 px-1 py-0.5 font-mono text-[0.85em] text-cyan">
        {children}
      </code>
    );
  },
  pre: ({ children }) => (
    <pre className="my-1.5 max-h-60 overflow-auto rounded-md border border-border-subtle bg-bg-deep/60 p-2.5">
      {children}
    </pre>
  ),
  table: ({ children }) => (
    <div className="my-1.5 overflow-x-auto">
      <table className="w-full border-collapse text-xs">{children}</table>
    </div>
  ),
  th: ({ children }) => (
    <th className="border border-border-subtle px-2 py-1 text-left font-mono text-fg-muted">
      {children}
    </th>
  ),
  td: ({ children }) => (
    <td className="border border-border-subtle px-2 py-1 tabular-nums">
      {children}
    </td>
  ),
  hr: () => <hr className="my-2 border-border-subtle" />,
};

export function ChatMarkdown({ children }: { children: string }) {
  return (
    <ReactMarkdown remarkPlugins={[remarkGfm]} components={COMPONENTS}>
      {children}
    </ReactMarkdown>
  );
}
