"use client";

import { useEffect, useMemo, useState } from "react";
import { ChevronLeft, ChevronRight } from "lucide-react";

import { cn } from "@/lib/cn";

/**
 * 客户端分页 hook —— 长列表(决策/日志/成交)一次拉回后分页渲染,
 * 避免上千行 DOM 一次性挂载。列表长度变化时自动钳制当前页。
 */
export function usePager<T>(items: T[], pageSize: number) {
  const [page, setPage] = useState(0);
  const pageCount = Math.max(1, Math.ceil(items.length / pageSize));

  // 数据刷新(SWR 轮询)导致页数变少时,钳回最后一页而不是停在空页。
  useEffect(() => {
    if (page > pageCount - 1) setPage(pageCount - 1);
  }, [page, pageCount]);

  const pageItems = useMemo(
    () => items.slice(page * pageSize, (page + 1) * pageSize),
    [items, page, pageSize],
  );

  return { page, setPage, pageCount, pageItems, total: items.length };
}

/** 分页脚条 —— 上一页/下一页 + 当前页码;单页时不渲染。 */
export function Pager({
  page,
  pageCount,
  onChange,
  className,
}: {
  page: number;
  pageCount: number;
  onChange: (page: number) => void;
  className?: string;
}) {
  if (pageCount <= 1) return null;
  return (
    <div
      className={cn(
        "flex items-center justify-end gap-2 border-t border-border-subtle/60 px-4 py-2",
        className,
      )}
    >
      <PageButton
        disabled={page === 0}
        onClick={() => onChange(page - 1)}
        ariaLabel="prev"
      >
        <ChevronLeft className="size-3.5" />
      </PageButton>
      <span className="tnum font-mono text-[11px] text-fg-muted">
        {page + 1} / {pageCount}
      </span>
      <PageButton
        disabled={page === pageCount - 1}
        onClick={() => onChange(page + 1)}
        ariaLabel="next"
      >
        <ChevronRight className="size-3.5" />
      </PageButton>
    </div>
  );
}

function PageButton({
  disabled,
  onClick,
  ariaLabel,
  children,
}: {
  disabled: boolean;
  onClick: () => void;
  ariaLabel: string;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      aria-label={ariaLabel}
      disabled={disabled}
      onClick={onClick}
      className={cn(
        "rounded-md border border-border-subtle p-1 text-fg-muted transition-colors",
        disabled
          ? "cursor-not-allowed opacity-35"
          : "hover:border-cyan/40 hover:text-cyan",
      )}
    >
      {children}
    </button>
  );
}
