"use client";

import { useTranslations } from "next-intl";

import { cn } from "@/lib/cn";
import { HexagramViz } from "./HexagramViz";
import { TarotCards } from "./TarotCards";
import type { DivinationView } from "./types";

/**
 * 玄学结果卡片 —— 调度六爻 / 塔罗渲染 + 强制免责条。
 *
 * 在对话栏(ChatThread)与独立占卜台(DivinationClient)复用同一组件;
 * 数据来自 `divination.*` tool 的返回(单一数据源在后端引擎)。
 *
 * @param reading 已解析的玄学结果(hexagram | tarot)
 * @param className 额外样式
 */
export function DivinationCard({
  reading,
  className,
}: {
  reading: DivinationView;
  className?: string;
}) {
  const t = useTranslations("divination");

  return (
    <div
      className={cn(
        "rise w-full overflow-hidden rounded-xl border border-seal/30 bg-bg-elev/40 backdrop-blur-sm",
        className,
      )}
    >
      {/* 头部:稻荷狐神朱红印章 + 类型标题 */}
      <div className="flex items-center gap-2 border-b border-border-subtle px-4 py-2.5">
        <img
          src="/inalpha-seal.png"
          alt=""
          width={20}
          height={20}
          className="seal-glow size-5 shrink-0 select-none"
          draggable={false}
        />
        <span className="font-display text-sm text-fg">
          {reading.kind === "hexagram" ? t("hexagramTitle") : t("tarotTitle")}
        </span>
      </div>

      <div className="px-4 py-4">
        {reading.kind === "hexagram" ? (
          <HexagramViz
            primary={reading.primary}
            changed={reading.changed}
            changingLines={reading.changingLines}
          />
        ) : (
          <TarotCards cards={reading.cards} />
        )}
      </div>

      {/* 免责条 —— 永远显示 */}
      <p className="border-t border-border-subtle bg-bg-deep/40 px-4 py-2 text-center font-mono text-[10px] text-fg-muted/70">
        {t("disclaimer")}
      </p>
    </div>
  );
}
