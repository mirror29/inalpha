import * as React from "react";

import { cn } from "@/lib/cn";

/** 看板里的密集数据表共用原子 —— 等宽数字、hairline 行分隔、右对齐数值列。 */

export function Th({
  children,
  right,
}: {
  children: React.ReactNode;
  right?: boolean;
}) {
  return (
    <th
      className={cn(
        // nowrap:列挤时表头不折行(难读),宽表整体走容器的 overflow-x 滚动。
        "whitespace-nowrap px-4 py-2 font-normal",
        right ? "text-right" : "text-left",
      )}
    >
      {children}
    </th>
  );
}

export function Td({
  children,
  right,
  mono,
  muted,
  className,
}: {
  children: React.ReactNode;
  right?: boolean;
  mono?: boolean;
  muted?: boolean;
  className?: string;
}) {
  return (
    <td
      className={cn(
        "px-4 py-2.5",
        right ? "text-right" : "text-left",
        mono && "tnum font-mono",
        muted && "text-fg-muted",
        className,
      )}
    >
      {children}
    </td>
  );
}

export function TableHeadRow({ children }: { children: React.ReactNode }) {
  return (
    <tr className="text-left font-mono text-[10px] uppercase tracking-wider text-fg-muted/70">
      {children}
    </tr>
  );
}

export function TableEmpty({ children }: { children: React.ReactNode }) {
  return (
    <div className="px-4 py-10 text-center text-sm text-fg-muted/70">
      {children}
    </div>
  );
}
