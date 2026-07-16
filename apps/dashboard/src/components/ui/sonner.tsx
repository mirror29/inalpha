/**
 * shadcn-style Sonner Toast 组件
 */
"use client";

import { Toaster as Sonner, toast } from "sonner";

type ToasterProps = React.ComponentProps<typeof Sonner>;

const Toaster = ({ ...props }: ToasterProps) => {
  return (
    <Sonner
      className="toaster group"
      toastOptions={{
        classNames: {
          toast:
            "group toast group-[.toaster]:bg-bg-elev group-[.toaster]:text-fg group-[.toaster]:border-border-subtle group-[.toaster]:shadow-lg",
          description: "group-[.toast]:text-fg-muted",
          actionButton:
            "group-[.toast]:bg-cyan/15 group-[.toast]:text-cyan group-[.toast]:hover:bg-cyan/25",
          cancelButton:
            "group-[.toast]:bg-bg-elev/60 group-[.toast]:text-fg-muted group-[.toast]:hover:bg-bg-elev",
          success: "group-[.toast]:border-bull/40 group-[.toast]:bg-bull/10",
          error: "group-[.toast]:border-fox-red/40 group-[.toast]:bg-fox-red/10",
          warning: "group-[.toast]:border-gold/40 group-[.toast]:bg-gold/10",
          info: "group-[.toast]:border-cyan/40 group-[.toast]:bg-cyan/10",
        },
      }}
      {...props}
    />
  );
};

export { Toaster, toast };