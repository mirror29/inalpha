import { cn } from "@/lib/cn";

type Tone = "bull" | "fox" | "gold" | "cyan" | "muted";

const toneCls: Record<Tone, string> = {
  bull: "border-bull/30 bg-bull/10 text-bull",
  fox: "border-fox-red/30 bg-fox-red/10 text-fox-red",
  gold: "border-gold/30 bg-gold/10 text-gold",
  cyan: "border-cyan/30 bg-cyan/10 text-cyan",
  muted: "border-border-subtle bg-bg-elev/40 text-fg-muted",
};

/** 订单状态 → 语义色(成交绿 / 拒单红 / 在途金 / 其余灰)。 */
function orderTone(status: string): Tone {
  const s = status.toUpperCase();
  if (s === "FILLED") return "bull";
  if (s === "REJECTED" || s === "CANCELED" || s === "EXPIRED") return "fox";
  if (s === "PARTIALLY_FILLED") return "gold";
  if (s === "NEW" || s === "SUBMITTED" || s === "ACCEPTED") return "cyan";
  return "muted";
}

/** Live runner 状态 → 语义色。 */
function runTone(status: string): Tone {
  if (status === "running") return "bull";
  if (status === "errored") return "fox";
  return "muted";
}

interface StatusBadgeProps {
  label: string;
  tone?: Tone;
  /** 左侧小圆点(running 用脉冲)。 */
  dot?: boolean;
  pulse?: boolean;
  className?: string;
}

export function StatusBadge({
  label,
  tone = "muted",
  dot = false,
  pulse = false,
  className,
}: StatusBadgeProps) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-md border px-2 py-0.5 font-mono text-[10px] uppercase tracking-wider",
        toneCls[tone],
        className,
      )}
    >
      {dot && (
        <span className="relative flex size-1.5">
          {pulse && (
            <span className="absolute inline-flex size-full animate-ping rounded-full bg-current opacity-60" />
          )}
          <span className="relative inline-flex size-1.5 rounded-full bg-current" />
        </span>
      )}
      {label}
    </span>
  );
}

export function OrderStatusBadge({ status }: { status: string }) {
  return <StatusBadge label={status} tone={orderTone(status)} />;
}

export function RunStatusBadge({ status }: { status: string }) {
  const tone = runTone(status);
  return (
    <StatusBadge
      label={status}
      tone={tone}
      dot
      pulse={status === "running"}
    />
  );
}

/** 决策撮合结果 → 语义色(成交绿 / 风控拒红 / 其他拒灰红)。 */
export function DecisionOutcomeBadge({
  outcome,
  label,
}: {
  outcome: string;
  label: string;
}) {
  const tone: Tone =
    outcome === "filled"
      ? "bull"
      : outcome === "risk_rejected"
        ? "fox"
        : "gold";
  return <StatusBadge label={label} tone={tone} />;
}
