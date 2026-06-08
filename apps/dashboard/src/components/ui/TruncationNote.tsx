import { Info } from "lucide-react";

/**
 * 列表/事件流命中显示上限时的「已截断」提示。
 *
 * 用于「不静默截断」原则:面板只显示最近 N 条时,显式告诉用户还有更早的未列出,
 * 避免「看着像全部其实被裁了」。文案由调用方按 namespace 传入(通常 common.truncated)。
 */
export function TruncationNote({ text }: { text: string }) {
  return (
    <div className="flex items-start gap-2.5 rounded-lg border border-border-subtle/60 bg-bg-elev/30 px-4 py-2.5">
      <Info className="mt-0.5 size-4 shrink-0 text-fg-muted" strokeWidth={2} />
      <p className="text-sm text-fg-muted">{text}</p>
    </div>
  );
}
