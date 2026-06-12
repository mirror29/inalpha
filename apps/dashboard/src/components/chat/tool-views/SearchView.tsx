"use client";

import { hostOf, shortDate } from "./format";

/**
 * 检索类工具视图:web.search / web.search_news(results[])与 data 新闻(items[])。
 * 标题即外链,副行 host/publisher + 日期,摘要两行截断。
 */

interface SearchItem {
  title: string;
  url?: string;
  link?: string;
  snippet?: string;
  summary?: string;
  publisher?: string;
  published_at?: string | null;
}

export interface SearchShape {
  results?: SearchItem[];
  items?: SearchItem[];
  query?: string;
  backend?: string;
}

export function isSearch(v: unknown): v is SearchShape {
  const o = v as SearchShape;
  if (!o || typeof o !== "object") return false;
  const list = o.results ?? o.items;
  return (
    Array.isArray(list) && list.length > 0 && typeof list[0]?.title === "string"
  );
}

const ITEM_CAP = 10;

export function SearchView({ s }: { s: SearchShape }) {
  const list = (s.results ?? s.items ?? []).slice(0, ITEM_CAP);
  const total = (s.results ?? s.items ?? []).length;
  return (
    <div className="flex flex-col gap-1.5">
      {(s.query || s.backend) && (
        <div className="font-mono text-[10px] text-fg-muted/60">
          {s.query}
          {s.backend ? ` · ${s.backend}` : ""}
        </div>
      )}
      {list.map((it, i) => {
        // 仅放行 http(s):后端返回可能被搜索投毒注入 javascript: 等危险协议,
        // React 不做 href 白名单、rel 也拦不住当前页执行,非 http(s) 一律降级为纯文本。
        const raw = it.url ?? it.link ?? "";
        const href = /^https?:\/\//i.test(raw) ? raw : "";
        const desc = it.snippet ?? it.summary ?? "";
        return (
          <div key={`${href}-${i}`} className="min-w-0">
            {href ? (
              <a
                href={href}
                target="_blank"
                rel="noreferrer noopener"
                className="block truncate text-[11px] text-cyan underline-offset-2 hover:underline"
                title={it.title}
              >
                {it.title}
              </a>
            ) : (
              <div className="truncate text-[11px] text-fg">{it.title}</div>
            )}
            <div className="font-mono text-[9px] text-fg-muted/50">
              {href ? hostOf(href) : (it.publisher ?? "")}
              {it.publisher && href ? ` · ${it.publisher}` : ""}
              {it.published_at ? ` · ${shortDate(it.published_at)}` : ""}
            </div>
            {desc && (
              <p className="line-clamp-2 text-[10px] leading-relaxed text-fg-muted">
                {desc}
              </p>
            )}
          </div>
        );
      })}
      {total > list.length && (
        <div className="font-mono text-[10px] text-fg-muted/40">
          +{total - list.length} …
        </div>
      )}
    </div>
  );
}
