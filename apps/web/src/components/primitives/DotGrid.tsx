import { cn } from "@/lib/cn";

interface DotGridProps {
  className?: string;
  fade?: "radial" | "top" | "none";
}

export function DotGrid({ className, fade = "radial" }: DotGridProps) {
  return (
    <div
      aria-hidden
      className={cn(
        "pointer-events-none absolute inset-0 dot-grid",
        fade === "radial" &&
          "[mask-image:radial-gradient(ellipse_at_center,black_30%,transparent_75%)]",
        fade === "top" &&
          "[mask-image:linear-gradient(to_bottom,black,transparent_80%)]",
        className,
      )}
    />
  );
}
