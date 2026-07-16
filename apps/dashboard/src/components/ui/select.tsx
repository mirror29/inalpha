/**
 * shadcn-style Select 组件
 */
import * as React from "react";
import { cn } from "@/lib/cn";
import { ChevronDown } from "lucide-react";

export interface SelectProps extends React.SelectHTMLAttributes<HTMLSelectElement> {}

const Select = React.forwardRef<HTMLSelectElement, SelectProps>(
  ({ className, children, ...props }, ref) => {
    return (
      <div className="relative">
        <select
          className={cn(
            "flex h-9 w-full appearance-none rounded-md border border-border-subtle bg-bg-deep/60 px-3 py-2 pr-8 text-sm text-fg outline-none transition-colors focus:border-cyan/50 focus:outline-none disabled:cursor-not-allowed disabled:opacity-50",
            className
          )}
          ref={ref}
          {...props}
        >
          {children}
        </select>
        <ChevronDown className="pointer-events-none absolute right-2 top-1/2 size-4 -translate-y-1/2 text-fg-muted" strokeWidth={1.75} />
      </div>
    );
  }
);
Select.displayName = "Select";

export { Select };