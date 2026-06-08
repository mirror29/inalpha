"use client";

import { ChevronDown, ChevronUp, TriangleAlert } from "lucide-react";
import { useLocale, useTranslations } from "next-intl";
import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type PointerEvent as ReactPointerEvent,
} from "react";
import useSWR from "swr";

import { Link } from "@/i18n/navigation";
import { cn } from "@/lib/cn";
import { jsonFetcher } from "@/lib/fetcher";
import { fmtTime } from "@/lib/format";
import type { ActivityEvent, ActivityPayload, ActivityTone } from "@/lib/types";
import { KindTag } from "./KindTag";

/** 与活动页同档:8s 轮询。 */
const REFRESH_MS = 8000;
const LS_HEIGHT = "inalpha-activity-height";

/** 收起态 ticker 条的高度(px,h-9)—— 拖动换算时要减去它。 */
const TICKER_H = 36;
/** 展开日志面板的高度区间 —— 拖动时 clamp 到此范围。 */
const PANEL_MIN_HEIGHT = 96;
/** 面板最高占视口比例(留出顶部内容)。 */
const PANEL_MAX_RATIO = 0.85;
/** 首次展开的默认高度(视口 12vh)。 */
const PANEL_DEFAULT_RATIO = 0.12;

const TONE_TEXT: Record<ActivityTone, string> = {
  bull: "text-bull",
  fox: "text-fox-red",
  gold: "text-gold",
  cyan: "text-cyan",
  muted: "text-fg-muted",
};

/**
 * 全 dashboard 常驻底部活动日志(终端风)。
 *
 * - 收起态:底部一条 ticker —— LIVE 点 + 最新一条活动 + 事件计数。
 * - 展开态:向上展开紧凑日志,**主内容被顶上去**(footer 实测高度写入 --activity-h,
 *   globals.css 让 main padding-bottom 跟随,所以不是覆盖而是 reflow)。
 * - **可拖高**:面板上缘的分隔条可拖动调整高度(默认 20vh,持久化),与右侧对话栏同款交互。
 * - 每条很密(py-1 单行)。会话条可点 → 打开右侧对话栏并切到该会话(派发 inalpha:open-chat)。
 * - 复用 `/api/activity`(已含会话/调度/审批/决策/风控/订单),SWR 8s 轮询。
 */
export function ActivityFooter() {
  const t = useTranslations("footer");
  const locale = useLocale();
  const [expanded, setExpanded] = useState(false);
  const [height, setHeight] = useState(0);
  const [dragging, setDragging] = useState(false);
  const footerRef = useRef<HTMLElement>(null);

  const { data, error } = useSWR<ActivityPayload>(
    "/api/activity",
    jsonFetcher,
    { refreshInterval: REFRESH_MS, keepPreviousData: true },
  );

  // 恢复展开高度(无持久值则默认 12vh,clamp 到区间)。展开态**不持久化**:活动日志
  // 默认收起,每次进来都从收起开始(区别于右侧对话栏默认打开),仅本会话内可手动展开。
  useEffect(() => {
    const max = window.innerHeight * PANEL_MAX_RATIO;
    const saved = Number(localStorage.getItem(LS_HEIGHT));
    const initial = saved > 0 ? saved : window.innerHeight * PANEL_DEFAULT_RATIO;
    setHeight(Math.min(Math.max(initial, PANEL_MIN_HEIGHT), max));
  }, []);

  // 拖动中:打 data-activity-dragging,让 main padding-bottom 关过渡跟手。
  useEffect(() => {
    const root = document.documentElement;
    if (dragging) root.dataset.activityDragging = "true";
    else delete root.dataset.activityDragging;
  }, [dragging]);

  // 拖动调高:clamp 到 [min, 视口*max] 并持久化(ResizeObserver 会据此刷新 --activity-h)。
  const onHeightChange = useCallback((px: number) => {
    const max = window.innerHeight * PANEL_MAX_RATIO;
    const h = Math.min(Math.max(px, PANEL_MIN_HEIGHT), max);
    setHeight(h);
    localStorage.setItem(LS_HEIGHT, String(Math.round(h)));
  }, []);

  // 上缘分隔条拖动:指针到视口底的距离减去 ticker 高即为面板高。
  const startResize = useCallback(
    (e: ReactPointerEvent) => {
      e.preventDefault();
      setDragging(true);
      const move = (ev: PointerEvent) =>
        onHeightChange(window.innerHeight - ev.clientY - TICKER_H);
      const up = () => {
        setDragging(false);
        window.removeEventListener("pointermove", move);
        window.removeEventListener("pointerup", up);
      };
      window.addEventListener("pointermove", move);
      window.addEventListener("pointerup", up);
    },
    [onHeightChange],
  );

  // footer 实测高度 → --activity-h(收起≈一条,展开≈日志高度),驱动 main 让出底部并 reflow。
  useEffect(() => {
    const el = footerRef.current;
    if (!el) return;
    const root = document.documentElement;
    const apply = () => root.style.setProperty("--activity-h", `${el.offsetHeight}px`);
    apply();
    const ro = new ResizeObserver(apply);
    ro.observe(el);
    return () => {
      ro.disconnect();
      root.style.removeProperty("--activity-h");
    };
  }, []);

  const events = data?.events ?? [];
  const latest = events[0];
  const offline = Boolean(error) && !data;
  const downCount = data
    ? Object.values(data.sources).filter((ok) => !ok).length
    : 0;

  return (
    <footer
      ref={footerRef}
      className="activity-footer fixed bottom-0 left-0 right-0 z-20 font-mono"
    >
      {/* 展开面板 —— 紧凑日志,坐落在 ticker 之上,高度可拖 */}
      {expanded && (
        <div
          style={{ height: `${height}px` }}
          className="relative flex flex-col border-t border-border-subtle bg-bg-deep/97 backdrop-blur-md"
        >
          {/* 上缘拖动条 —— 调整面板高度,主内容同步 reflow。 */}
          <div
            onPointerDown={startResize}
            role="separator"
            aria-orientation="horizontal"
            aria-label={t("resize")}
            className="group absolute left-0 top-0 z-10 h-2 w-full -translate-y-1/2 cursor-row-resize touch-none before:absolute before:inset-x-0 before:top-1/2 before:h-px before:-translate-y-1/2 before:bg-transparent before:transition-colors before:content-[''] hover:before:bg-cyan/60"
          />
          <div className="flex shrink-0 items-center justify-between border-b border-border-subtle px-4 py-1.5">
            <span className="tick-accent pl-2.5 text-[11px] uppercase tracking-[0.18em] text-cyan">
              {t("title")}
            </span>
            <Link
              href="/activity"
              className="text-[10px] uppercase tracking-wider text-fg-muted transition-colors hover:text-cyan"
            >
              {t("viewAll")} →
            </Link>
          </div>
          <div className="min-h-0 flex-1 divide-y divide-border-subtle/40 overflow-y-auto px-2 py-1">
            {events.length === 0 ? (
              <p className="px-2 py-6 text-center text-xs text-fg-muted/70">
                {t("empty")}
              </p>
            ) : (
              events.map((e) => (
                <FooterRow key={e.id} event={e} locale={locale} />
              ))
            )}
          </div>
        </div>
      )}

      {/* 收起态 ticker —— 整条可点切换展开 */}
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        aria-label={expanded ? t("collapse") : t("expand")}
        title={expanded ? t("collapse") : t("expand")}
        aria-expanded={expanded}
        className="flex h-9 w-full items-center gap-3 border-t border-border-subtle bg-bg-deep/95 px-4 text-left backdrop-blur-md transition-colors hover:bg-bg-elev/60"
      >
        <span className="flex items-center gap-1.5 text-[10px] uppercase tracking-[0.18em] text-fg-muted/80">
          <span
            className={cn(
              "size-1.5 rounded-full",
              offline ? "bg-fox-red" : "bg-bull caret-blink",
            )}
          />
          <span className="hidden sm:inline">{t("title")}</span>
        </span>

        <span className="flex min-w-0 flex-1 items-center gap-2 text-[11px]">
          {latest ? (
            <>
              <span className="text-fg-muted/60 tabular-nums">
                {fmtTime(latest.ts, locale)}
              </span>
              <KindTag kind={latest.kind} />
              <span className="truncate text-fg">{latest.title}</span>
              {latest.outcome && (
                <span
                  className={cn(
                    "hidden shrink-0 uppercase tracking-wider md:inline",
                    TONE_TEXT[latest.tone],
                  )}
                >
                  {latest.outcome}
                </span>
              )}
            </>
          ) : (
            <span className="text-fg-muted/60">{t("idle")}</span>
          )}
        </span>

        <span className="flex shrink-0 items-center gap-3 text-[10px] text-fg-muted">
          {downCount > 0 && (
            <TriangleAlert className="size-3.5 text-gold" strokeWidth={2} />
          )}
          <span className="tabular-nums">
            {events.length} {t("events")}
          </span>
          {expanded ? (
            <ChevronDown className="size-4" strokeWidth={2} />
          ) : (
            <ChevronUp className="size-4" strokeWidth={2} />
          )}
        </span>
      </button>
    </footer>
  );
}

/** 一条紧凑日志行。会话条可点开对话;有 href 的可点进详情。 */
function FooterRow({
  event,
  locale,
}: {
  event: ActivityEvent;
  locale: string;
}) {
  const t = useTranslations("footer");
  const inner = (
    <>
      <time className="shrink-0 text-fg-muted/55 tabular-nums">
        {fmtTime(event.ts, locale)}
      </time>
      <KindTag kind={event.kind} />
      <span className="min-w-0 flex-1 truncate text-fg">{event.title}</span>
      {event.outcome && (
        <span
          className={cn(
            "hidden shrink-0 uppercase tracking-wider sm:inline",
            TONE_TEXT[event.tone],
          )}
        >
          {event.outcome}
        </span>
      )}
    </>
  );
  const cls =
    "flex w-full items-center gap-2 px-2 py-1 text-left text-[11px] leading-tight transition-colors hover:bg-bg-elev/50";

  if (event.kind === "conversation") {
    const threadId = event.id.replace(/^conv:/, "");
    return (
      <button
        type="button"
        title={t("openConversation")}
        onClick={() =>
          window.dispatchEvent(
            new CustomEvent("inalpha:open-chat", { detail: { threadId } }),
          )
        }
        className={cls}
      >
        {inner}
      </button>
    );
  }
  if (event.href) {
    return (
      <Link href={event.href} className={cls}>
        {inner}
      </Link>
    );
  }
  return <div className={cn(cls, "cursor-default hover:bg-transparent")}>{inner}</div>;
}
