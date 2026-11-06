"use client";

import * as React from "react";
import * as SelectPrimitive from "@radix-ui/react-select";
import { Check, ChevronDown, ChevronUp } from "lucide-react";
import { cn } from "@/lib/cn";

const Select = SelectPrimitive.Root;
const SelectValue = SelectPrimitive.Value;

/** Accessible shadcn-style trigger using the console's shared color tokens. */
const SelectTrigger = React.forwardRef<
  React.ComponentRef<typeof SelectPrimitive.Trigger>,
  React.ComponentPropsWithoutRef<typeof SelectPrimitive.Trigger>
>(({ className, children, ...props }, ref) => (
  <SelectPrimitive.Trigger ref={ref} className={cn(
    "flex h-9 w-full items-center justify-between gap-2 rounded-md border border-border-subtle bg-bg-deep/60 px-3 text-sm text-fg outline-none transition-colors focus-visible:border-cyan/50 focus-visible:ring-2 focus-visible:ring-cyan/20 disabled:cursor-not-allowed disabled:opacity-50 [&>span]:truncate",
    className,
  )} {...props}>
    {children}
    <SelectPrimitive.Icon asChild><ChevronDown className="size-4 shrink-0 text-fg-muted" /></SelectPrimitive.Icon>
  </SelectPrimitive.Trigger>
));
SelectTrigger.displayName = "SelectTrigger";

/** Portal keeps the menu above dialogs; Radix handles focus, typeahead and keyboard navigation. */
const SelectContent = React.forwardRef<
  React.ComponentRef<typeof SelectPrimitive.Content>,
  React.ComponentPropsWithoutRef<typeof SelectPrimitive.Content>
>(({ className, children, position = "popper", ...props }, ref) => (
  <SelectPrimitive.Portal>
    <SelectPrimitive.Content ref={ref} position={position} sideOffset={5} className={cn(
      "relative z-[100] max-h-[var(--radix-select-content-available-height)] min-w-[var(--radix-select-trigger-width)] overflow-hidden rounded-md border border-border-subtle bg-bg-elev text-fg shadow-lg",
      className,
    )} {...props}>
      <SelectPrimitive.ScrollUpButton className="flex items-center justify-center py-1"><ChevronUp className="size-4" /></SelectPrimitive.ScrollUpButton>
      <SelectPrimitive.Viewport className="p-1">{children}</SelectPrimitive.Viewport>
      <SelectPrimitive.ScrollDownButton className="flex items-center justify-center py-1"><ChevronDown className="size-4" /></SelectPrimitive.ScrollDownButton>
    </SelectPrimitive.Content>
  </SelectPrimitive.Portal>
));
SelectContent.displayName = "SelectContent";

/** Selected and disabled options share the same states as the console dropdown menus. */
const SelectItem = React.forwardRef<
  React.ComponentRef<typeof SelectPrimitive.Item>,
  React.ComponentPropsWithoutRef<typeof SelectPrimitive.Item>
>(({ className, children, ...props }, ref) => (
  <SelectPrimitive.Item ref={ref} className={cn(
    "relative flex w-full cursor-default select-none items-center rounded-sm py-2 pl-3 pr-8 text-sm outline-none data-[highlighted]:bg-cyan/10 data-[highlighted]:text-fg data-[disabled]:pointer-events-none data-[disabled]:opacity-50",
    className,
  )} {...props}>
    <SelectPrimitive.ItemText>{children}</SelectPrimitive.ItemText>
    <span className="absolute right-2 flex size-4 items-center justify-center"><SelectPrimitive.ItemIndicator><Check className="size-4 text-cyan" /></SelectPrimitive.ItemIndicator></span>
  </SelectPrimitive.Item>
));
SelectItem.displayName = "SelectItem";

export { Select, SelectTrigger, SelectValue, SelectContent, SelectItem };
