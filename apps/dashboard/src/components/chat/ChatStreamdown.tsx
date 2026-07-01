"use client";

import { type Components } from "streamdown";
import { Streamdown } from "streamdown";
import remarkGfm from "remark-gfm";

import { CHAT_LINK_SAFETY, createSecureRehypePlugins, STREAMDOWN_PLUGINS } from "./chat-streamdown-security";

/**
 * Streamdown 的 Components 类型与 react-markdown 兼容（key 和签名一致），
 * 直接从 ChatMarkdown 的样式映射迁移，保持「印章终端」主题。
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
    // 块级 code 带 language-* className；行内 code 没有。
    // @streamdown/code 插件已处理语法高亮，这里只加 seal 终端主题的容器样式。
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

/**
 * 流式 Markdown 渐进渲染。
 *
 * 用 streamdown（Apache 2.0）替换 react-markdown：
 * - 流式模式下逐 token 渐进渲染，而非等全文到达再一次性 parse
 * - @streamdown/code 提供语法高亮
 * - @streamdown/cjk 优化中日韩文字排版
 * - 保留「印章终端」主题的组件样式覆盖
 *
 * @param streaming 是否为流式输出（agent 正在生成）。静态模式用于历史消息回填。
 */
export function ChatStreamdown({
  children,
  streaming = false,
}: {
  children: string;
  streaming?: boolean;
}) {
  return (
    <Streamdown
      mode={streaming ? "streaming" : "static"}
      components={COMPONENTS}
      remarkPlugins={[remarkGfm]}
      rehypePlugins={createSecureRehypePlugins()}
      plugins={STREAMDOWN_PLUGINS}
      linkSafety={CHAT_LINK_SAFETY}
    >
      {children}
    </Streamdown>
  );
}
